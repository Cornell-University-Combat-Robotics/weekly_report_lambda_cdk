# lambda/lambda_function.py
import os
import http.client
import urllib.parse
import boto3
from botocore.exceptions import ClientError

def lambda_handler(event, context):
    # Check if the password is correct
    if event['password'] != os.environ.get('SLACK_PASSWORD'):
        return {
            'statusCode': 401,  # Unauthorized
            'headers': {
                'Access-Control-Allow-Origin': '*',  # Adjust as needed for production
            },
            'body': 'Invalid password'
        }

    # Initialize a boto3 client for DynamoDB
    dynamodb = boto3.resource('dynamodb')

    # Define the table name and the key of the item you want to retrieve and update
    table_name = os.environ.get('TABLE_NAME')
    table = dynamodb.Table(table_name)
    key_to_retrieve = {'sem': 'Fa24'}  # Replace 'Fa24' with the actual key

    try:
        # Retrieve the item from DynamoDB
        response = table.get_item(Key=key_to_retrieve)
        if 'Item' in response:
            week_num = int(response['Item']['week_num']) + event['increment']  # Increment the week_num
            
            # Update the item in DynamoDB
            table.update_item(
                Key=key_to_retrieve,
                UpdateExpression='SET week_num = :val',
                ExpressionAttributeValues={
                    ':val': week_num
                }
            )
        else:
            # Handle the case where the item does not exist
            week_num = 1  # Start with a default value or handle accordingly
            # Optionally, create the item if it does not exist
            table.put_item(Item={'sem': 'Fa24', 'week_num': week_num})  # Replace 'Fa23' with the actual key
    except ClientError as e:
        # Handle the error
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',  # Adjust as needed for production
            },
            'body': f"Error accessing DynamoDB: {e.response['Error']['Message']}"
        }

    # Combine the DynamoDB value with the event data
    combined_text = f"WEEK {week_num} {event['data']}"

    # URL encode the parameters
    params = urllib.parse.urlencode({
        'channel': os.environ.get('SLACK_CHANNEL'),
        'text': combined_text
    })

    # Set up the headers for the HTTPS request
    headers = {
        'Authorization': f"Bearer {os.environ.get('SLACK_TOKEN')}",
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    # Establish a connection to the Slack API
    conn = http.client.HTTPSConnection("slack.com")
    conn.request("POST", "/api/chat.postMessage", params, headers)
    
    # Get the response from the Slack API
    response = conn.getresponse()
    data = response.read().decode()
    conn.close()

    # Check the status of the response and return appropriate result
    if response.status == 200:
        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',  # Adjust as needed for production
            },
            'body': data
        }
    else:
        return {
            'statusCode': response.status,
            'headers': {
                'Access-Control-Allow-Origin': '*',  # Adjust as needed for production
            },
            'body': f"Error: {data}"
        }
