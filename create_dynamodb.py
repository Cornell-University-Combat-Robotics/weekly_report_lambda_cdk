import boto3
from botocore.exceptions import ClientError

# Initialize DynamoDB client
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')  # Change region if needed

# Table name
table_name = 'weekly_report_info'

try:
    # Create the table
    table = dynamodb.create_table(
        TableName=table_name,
        KeySchema=[
            {
                'AttributeName': 'sem2week_num',
                'KeyType': 'HASH'  # Partition key
            }
        ],
        AttributeDefinitions=[
            {
                'AttributeName': 'sem2week_num',
                'AttributeType': 'N'  # Number type for integer
            }
        ],
        ProvisionedThroughput={
            'ReadCapacityUnits': 5,
            'WriteCapacityUnits': 5
        }
    )

    # Wait for the table to be created
    print("Creating table. Please wait...")
    table.meta.client.get_waiter('table_exists').wait(TableName=table_name)
    print(f"Table '{table_name}' created successfully.")

    # Insert default item
    default_item = {
        'sem2week_num': 0,
        'Autonomous': "",
        'Kinetic': "",
        'Sportsman': "",
        'Marketing': "",
        'TL': "",
        'Full_Team': "",
        'Weekday': "SUNDAY",
        'Password': "CRCSlackBot"
    }

    # Put the item into the table
    table.put_item(Item=default_item)
    print("Default item inserted successfully.")

except ClientError as e:
    if e.response['Error']['Code'] == 'ResourceInUseException':
        print(f"Table '{table_name}' already exists.")
    else:
        print(f"Unexpected error: {e}")

