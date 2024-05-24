import boto3
import os

def handler(event, context):
    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ['TABLE_NAME']
    table = dynamodb.Table(table_name)

    # Insert the initial item
    table.put_item(Item={'sem': 'Fa23', 'week_num': 0})

    return {
        'statusCode': 200,
        'body': 'Initial item inserted'
    }
