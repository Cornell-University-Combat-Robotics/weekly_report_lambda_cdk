#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { WeeklyReportLambdaCdkStack } from '../lib/weekly_report_lambda_cdk-stack';

const app = new cdk.App();
new WeeklyReportLambdaCdkStack(app, 'WeeklyReportLambdaCdkStack');
