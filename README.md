# Welcome to the Weekly Rreport Bot Service CDK TypeScript project

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

### Steps to Create a CDK Project and Deploy a Lambda Function

* `mkdir my-cdk`
* `cd my-cdk`
* `cdk init app --language typescript`
