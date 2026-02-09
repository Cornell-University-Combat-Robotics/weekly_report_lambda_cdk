import boto3

lambda_client = boto3.client('lambda')

function_name = 'WeeklyReportReminder2'
role_arn = 'arn:aws:iam::891377090032:role/lambda-execution-role'

with open('function.zip', 'rb') as f:
    zipped_code = f.read()

response = lambda_client.create_function(
    FunctionName=function_name,
    Runtime='python3.12',
    Role=role_arn,
    Handler='lambda_function.lambda_handler',
    Code={'ZipFile': zipped_code},
    Description='Lambda created using Boto3',
    Timeout=10,
    MemorySize=128,
    Publish=True
)

print(f"Lambda Function ARN: {response['FunctionArn']}")
