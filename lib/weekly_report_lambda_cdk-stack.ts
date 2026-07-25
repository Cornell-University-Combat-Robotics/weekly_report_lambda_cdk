import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import {
  HttpApi,
  HttpMethod,
  CorsHttpMethod,
} from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as dotenv from 'dotenv';

dotenv.config();

/**
 * Date-driven plan + dispatcher architecture (see REDESIGN.md).
 *
 *   Planner    — Aug 1 / Jan 1 → writes 16-week plan to DynamoDB
 *   Refresher  — Thursday      → syncs Slack rosters into upcoming weeks
 *   Dispatcher — daily crons   → reads today's plan entry, posts to Slack
 *   API        — HTTP API      → GET /plan, POST /plan/week (passphrase-gated)
 */
export class WeeklyReportLambdaCdkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ---------------------------------------------------------------------
    // DynamoDB: per-week plan items, PK sem (e.g. "Sp26"), SK week (1..16)
    // ---------------------------------------------------------------------
    const planTable = new dynamodb.Table(this, 'WeeklyReportPlanTable', {
      partitionKey: { name: 'sem', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'week', type: dynamodb.AttributeType.NUMBER },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // One asset shared by all four functions, built by scripts/build-lambda.sh.
    // Bundling build/lambda rather than lambda/ means credentials and test
    // fixtures cannot reach the package by construction -- the build step only
    // ever copies the four handlers in, so there is no exclude list to keep in
    // sync and forget. Run `npm run build:lambda` before synth or deploy.
    const lambdaCode = lambda.Code.fromAsset('build/lambda', {
      exclude: ['__pycache__', '*.pyc'],
    });

    // Name only -- the key itself is stored in Secrets Manager out of band.
    const googleSecretName = process.env.GOOGLE_SA_SECRET ?? '';

    const commonEnv = { TABLE_NAME: planTable.tableName };
    const slackEnv = {
      SLACK_TOKEN: process.env.SLACK_TOKEN ?? '',
      SLACK_CHANNEL: process.env.SLACK_CHANNEL ?? 'tasks-weekly-report',
      SLACK_CHANNEL_ID: process.env.SLACK_CHANNEL_ID ?? '',
    };

    // ---------------------------------------------------------------------
    // Lambdas
    // ---------------------------------------------------------------------
    const plannerFn = new lambda.Function(this, 'PlannerLambda', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'planner.lambda_handler',
      code: lambdaCode,
      timeout: cdk.Duration.minutes(2),
      memorySize: 512,
      environment: { ...commonEnv },
    });
    planTable.grantReadWriteData(plannerFn);

    const refresherFn = new lambda.Function(this, 'RefresherLambda', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'refresher.lambda_handler',
      code: lambdaCode,
      timeout: cdk.Duration.minutes(2),
      memorySize: 512,
      environment: {
        ...commonEnv,
        MEMBER_SHEET_ID: process.env.MEMBER_SHEET_ID ?? '',
        MEMBER_SHEET_GRID: process.env.MEMBER_SHEET_GRID ?? '0',
        MEMBER_SHEET_RANGE: process.env.MEMBER_SHEET_RANGE ?? 'A:Z',
        // Set => read the sheet as the service account (private sheet).
        // Unset => fall back to the public CSV export.
        GOOGLE_SA_SECRET: googleSecretName,
        // Optional allowlist; unset means "use whatever subteams the sheet has".
        SUBTEAMS: process.env.SUBTEAMS ?? '',
      },
    });
    planTable.grantReadWriteData(refresherFn);

    // The Google service-account key lives in Secrets Manager, never in the
    // deployment bundle. The secret is created out of band (see README) so no
    // private key passes through CDK or CloudFormation.
    if (googleSecretName) {
      const googleSecret = secretsmanager.Secret.fromSecretNameV2(
        this,
        'GoogleServiceAccountSecret',
        googleSecretName,
      );
      googleSecret.grantRead(refresherFn);
    }

    const dispatcherFn = new lambda.Function(this, 'DispatcherLambda', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'dispatcher.lambda_handler',
      code: lambdaCode,
      timeout: cdk.Duration.minutes(2),
      memorySize: 512,
      environment: { ...commonEnv, ...slackEnv },
    });
    planTable.grantReadData(dispatcherFn);

    const apiFn = new lambda.Function(this, 'ApiLambda', {
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'api.lambda_handler',
      code: lambdaCode,
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        ...commonEnv,
        EDIT_PASSPHRASE: process.env.EDIT_PASSPHRASE ?? '',
        PAGES_ORIGIN: process.env.PAGES_ORIGIN ?? '*',
      },
    });
    planTable.grantReadWriteData(apiFn);

    // ---------------------------------------------------------------------
    // API Gateway HTTP API in front of apiFn
    // ---------------------------------------------------------------------
    const pagesOrigin = process.env.PAGES_ORIGIN ?? '*';
    const httpApi = new HttpApi(this, 'WeeklyReportHttpApi', {
      apiName: 'WeeklyReportApi',
      corsPreflight: {
        allowOrigins: [pagesOrigin],
        allowMethods: [
          CorsHttpMethod.GET,
          CorsHttpMethod.POST,
          CorsHttpMethod.OPTIONS,
        ],
        allowHeaders: ['content-type'],
      },
    });

    const apiIntegration = new HttpLambdaIntegration('ApiIntegration', apiFn);
    httpApi.addRoutes({
      path: '/plan',
      methods: [HttpMethod.GET],
      integration: apiIntegration,
    });
    httpApi.addRoutes({
      path: '/plan/week',
      methods: [HttpMethod.POST],
      integration: apiIntegration,
    });
    httpApi.addRoutes({
      path: '/plan/reset',
      methods: [HttpMethod.POST],
      integration: apiIntegration,
    });

    new cdk.CfnOutput(this, 'ApiUrl', { value: httpApi.apiEndpoint });
    new cdk.CfnOutput(this, 'PlanTableName', { value: planTable.tableName });

    // ---------------------------------------------------------------------
    // EventBridge Scheduler — one role allowed to invoke the three triggered
    // lambdas (api is invoked through API Gateway, not on a schedule).
    // ---------------------------------------------------------------------
    const schedulerRole = new iam.Role(this, 'SchedulerInvokeRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
    });
    schedulerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['lambda:InvokeFunction'],
      resources: [
        plannerFn.functionArn,
        refresherFn.functionArn,
        dispatcherFn.functionArn,
      ],
    }));

    // Annual planner runs — Aug 1 (fall) and Jan 1 (spring) at 09:00 ET.
    const plannerSchedules: Array<{ id: string; expr: string }> = [
      { id: 'PlannerAug1', expr: 'cron(0 9 1 8 ? *)' },
      { id: 'PlannerJan1', expr: 'cron(0 9 1 1 ? *)' },
    ];
    plannerSchedules.forEach(({ id, expr }) => {
      new scheduler.CfnSchedule(this, id, {
        scheduleExpression: expr,
        scheduleExpressionTimezone: 'America/New_York',
        flexibleTimeWindow: { mode: 'OFF' },
        target: {
          arn: plannerFn.functionArn,
          roleArn: schedulerRole.roleArn,
        },
      });
    });

    // Weekly roster refresher — Thursdays at 03:00 ET (self-gates outside semester).
    new scheduler.CfnSchedule(this, 'RefresherThursday', {
      scheduleExpression: 'cron(0 3 ? * 5 *)',
      scheduleExpressionTimezone: 'America/New_York',
      flexibleTimeWindow: { mode: 'OFF' },
      target: {
        arn: refresherFn.functionArn,
        roleArn: schedulerRole.roleArn,
      },
    });

    // Daily dispatcher crons — 08:00, 18:00, 19:00, 21:00, 23:00 ET.
    // Out-of-semester days are no-ops (no plan entry for today).
    const dispatcherHours = [8, 18, 19, 21, 23];
    dispatcherHours.forEach((hour) => {
      const pad = String(hour).padStart(2, '0');
      new scheduler.CfnSchedule(this, `Dispatcher${pad}00`, {
        scheduleExpression: `cron(0 ${hour} * * ? *)`,
        scheduleExpressionTimezone: 'America/New_York',
        flexibleTimeWindow: { mode: 'OFF' },
        target: {
          arn: dispatcherFn.functionArn,
          roleArn: schedulerRole.roleArn,
        },
      });
    });
  }
}
