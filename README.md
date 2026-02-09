# Welcome to the Weekly Report Bot Service CDK TypeScript project

Weekly report Slack bot: **Fridays** it posts initial threads (TL, Marketing, Autonomous, Kinetic, Sportsman) so people can reply with images/PDFs. **Sundays at 11pm** it reminds only users who have not submitted (no file in any Friday thread).

## Lambda dependencies (required for Sunday reminder)

The Sunday reminder uses `slack_sdk` and `pytz`. Before deploying, install them into the Lambda asset:

```bash
cd lambda && pip install -r requirements.txt -t . && cd ..
```

Then run `npm run build`, `cdk deploy` as usual.

## Slack channel ID for reminder

For the Sunday “who hasn’t submitted” logic, the Lambda uses Slack’s `conversations_history` and `conversations_members`, which require the **channel ID** (e.g. `C03UYNDBUPQ`). Set `SLACK_CHANNEL_ID` when deploying, e.g. in `.env`:

```
SLACK_CHANNEL_ID=C03UYNDBUPQ
```

You can get the channel ID in Slack: open the channel → channel name → “Copy link” or channel details.

## To Initiate the Whole Service in the Default Configured Account, Run in the Root Directory:

* `cdk bootstrap`
* `cdk deploy`

## To Deploy Subsequent Local Changes to the Existing Service in the Default Configured Account, Run in the Root Directory:

* `npm run build`
* `cdk synth`
* `cdk deploy`

## To Remove the Existing Service in the Default Configured Account, Run in the Root Directory:

* `cdk destroy`

## Additionally, to Create a new CDK Project:

To create a CDK (Cloud Development Kit) for deploying a Lambda function in your AWS account, you can follow these steps. I'll guide you through the process using the AWS CDK with TypeScript, as it's a common choice. Ensure you have Node.js, AWS CLI, and AWS CDK installed on your Mac.

### Prerequisites
1. **Node.js and npm**: Ensure you have Node.js and npm installed. You can install it using Homebrew:
  * `brew install node`

2. **AWS CLI**: Install the AWS CLI and configure it with your AWS credentials:
  * `brew install awscli`
  * `aws configure`

3. **AWS CDK**: Install the AWS CDK globally using npm:
  * `npm install -g aws-cdk`

### Steps to Create a CDK Project

* `mkdir my-cdk`
* `cd my-cdk`
* `cdk init app --language typescript`
