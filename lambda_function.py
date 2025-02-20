import os
import http.client
import urllib.parse
import boto3
from botocore.exceptions import ClientError

def lambda_handler(event, context):
    # Initialize a boto3 client for DynamoDB
    dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION'))
    table_name = 'weekly_report_info'
    table = dynamodb.Table(table_name)

    try:
        # Retrieve from DynamoDB
        response = table.scan()

        if 'Items' not in response or len(response['Items']) == 0:
            return {
                'statusCode': 404,
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                },
                'body': 'No items found in DynamoDB'
        }


        # Extract the first item (assuming only one item for password & weekday check)
        item = response['Items'][0]

        # Check password from DynamoDB
        dynamo_password = item.get('Password', '')
        if os.environ.get('PASSWORD') != dynamo_password:
            return {
                'statusCode': 401,  # Unauthorized
                'headers': {
                    'Access-Control-Allow-Origin': '*',
                },
                'body': 'Invalid password'
            }


        # Extract the Weekday
        sem2week_num_key = item.get('sem2week_num')
        current_weeknum = int(item.get('week_num',0))
        new_weeknum = current_weeknum + 1
        # Update the item in DynamoDB
        table.update_item(
            Key={'sem2week_num': sem2week_num_key},
            UpdateExpression='SET week_num = :val',
            ExpressionAttributeValues={
                ':val': new_weeknum
            }
        )
        weekday = item.get('Weekday', 'SUNDAY')

        # Define the teams and their corresponding message fields
        teams = ['Autonomous', 'TL', 'Kinetic', 'Sportsman', 'Marketing']

        # Prepare a list to collect post statuses
        post_statuses = []

        # Post a message for each team
        for team in teams:
            team_message = item.get(team, f"No message for {team}")
            combined_text = f"Week {current_weeknum} {team} thread for Weekly Report. DUE AT 11:59 PM ON {weekday}. {team_message}"

            # URL encode the parameters
            params = urllib.parse.urlencode({
                'channel': os.environ.get('SLACK_CHANNEL'),
                'text': combined_text
            })

            # Set up the headers for Slack API
            headers = {
                'Authorization': f"Bearer {os.environ.get('SLACK_TOKEN')}",
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            # Establish connection to Slack API
            conn = http.client.HTTPSConnection("slack.com")
            conn.request("POST", "/api/chat.postMessage", params, headers)

            # Get Slack API response
            slack_response = conn.getresponse()
            data = slack_response.read().decode()
            conn.close()

            # Store the result for each message
            post_statuses.append({
                'team': team,
                'status': slack_response.status,
                'response': data
            })

        return {
            'statusCode': 200,
            'headers': {
                'Access-Control-Allow-Origin': '*',
            },
            'body': f"Messages posted: {post_statuses}"
        }

    except ClientError as e:
        return {
            'statusCode': 500,
            'headers': {
                'Access-Control-Allow-Origin': '*',
            },
            'body': f"Error accessing DynamoDB: {e.response['Error']['Message']}"
        }