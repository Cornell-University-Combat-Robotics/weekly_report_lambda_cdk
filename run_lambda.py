import boto3
import json

lambda_client = boto3.client('lambda')

response = lambda_client.invoke(
    FunctionName='WeeklyReportReminder2',
    InvocationType='RequestResponse',
    Payload=json.dumps({})
)

print("Lambda Response:", response['Payload'].read().decode())
