import boto3
import json

iam_client = boto3.client('iam')

role_name = 'lambda-execution-role'
assume_role_policy = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "lambda.amazonaws.com"},
        "Action": "sts:AssumeRole"
    }]
})

response = iam_client.create_role(
    RoleName=role_name,
    AssumeRolePolicyDocument=assume_role_policy,
    Description='IAM role for Lambda execution'
)

# Attach basic execution policy
iam_client.attach_role_policy(
    RoleName=role_name,
    PolicyArn='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
)

print(f"Role ARN: {response['Role']['Arn']}")
