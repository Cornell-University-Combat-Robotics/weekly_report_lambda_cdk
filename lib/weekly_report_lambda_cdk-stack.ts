import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as dotenv from 'dotenv';

// Load environment variables from .env file
dotenv.config();

export class WeeklyReportLambdaCdkStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Define the DynamoDB table
    const table = new dynamodb.Table(this, 'sem2week_num', {
      partitionKey: { name: 'sem', type: dynamodb.AttributeType.STRING },
      removalPolicy: cdk.RemovalPolicy.DESTROY, // NOT recommended for production code
    });

    // Define the Lambda function
    const myLambda = new lambda.Function(this, 'GeneralReminderLambdaFunction', {
      runtime: lambda.Runtime.PYTHON_3_9, // Specify the runtime environment
      handler: 'lambda_function.lambda_handler', // The handler method in your Lambda function code
      code: lambda.Code.fromAsset('lambda'), // Path to the directory containing your Lambda function code
      timeout: cdk.Duration.minutes(1), // Set the timeout to 1 minute
    });

    // Add environment variables
    myLambda.addEnvironment('SLACK_PASSWORD', 'CRCSlackBot');
    myLambda.addEnvironment('SLACK_CHANNEL', 'tasks-weekly-report');
    myLambda.addEnvironment('SLACK_TOKEN', process.env.SLACK_TOKEN!);
    myLambda.addEnvironment('TABLE_NAME', table.tableName);

    // // Grant the Lambda function necessary permissions to access DynamoDB
    // myLambda.addToRolePolicy(new iam.PolicyStatement({
    //   actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem', 'dynamodb:PutItem'],
    //   resources: ['*'],
    // }));

    // Grant the Lambda function necessary permissions to access DynamoDB
    table.grantReadWriteData(myLambda);

    // Define the Lambda function for inserting the initial item
    const initItemLambda = new lambda.Function(this, 'InitItemLambda', {
      runtime: lambda.Runtime.PYTHON_3_9,
      handler: 'insert_initial_item.handler',
      code: lambda.Code.fromAsset('custom-resources'),
      environment: {
        TABLE_NAME: table.tableName,
      },
    });

    // Grant the initial item Lambda function permissions to write to the DynamoDB table
    table.grantWriteData(initItemLambda);

    // Create a custom resource provider
    const provider = new cr.Provider(this, 'InitItemProvider', {
      onEventHandler: initItemLambda,
    });

    // Create a custom resource to invoke the Lambda function after the table is created
    new cdk.CustomResource(this, 'InsertInitialItem', {
      serviceToken: provider.serviceToken,
    });

    // Create an IAM role for EventBridge Scheduler
    const schedulerRole = new iam.Role(this, 'SchedulerRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
    });

    // Attach a policy to the role that allows invoking the Lambda function
    schedulerRole.addToPolicy(new iam.PolicyStatement({
      actions: ['lambda:InvokeFunction'],
      resources: [myLambda.functionArn],
    }));

    // Create an EventBridge schedule to trigger the Lambda function
    new scheduler.CfnSchedule(this, 'TLWeeklyReportThreadSchedule', {
      scheduleExpression: 'cron(0 8 ? * 2 *)', // Every Tuesday at 8:00 AM
      flexibleTimeWindow: {
        mode: 'OFF',
      },
      target: {
        arn: myLambda.functionArn,
        roleArn: schedulerRole.roleArn,
        input: JSON.stringify({ key: 'value' }), // Adjust as needed
      },
    });
  }
}
