# import libraries
from google.auth import load_credentials_from_file
from googleapiclient.discovery import build
import boto3 # AWS SDK for Python
import os
from dotenv import load_dotenv
import pytz
from datetime import datetime, timedelta, timezone


# Initialize clients and google api
creds, project = load_credentials_from_file('googleapi.json')
service = build('sheets', 'v4', credentials=creds)
scheduler_client = boto3.client('scheduler', region_name='us-east-1')
lambda_client = boto3.client('lambda')


# Get secrets variables from Google Sheets and AWS
load_dotenv()
SPREADSHEET_ID = '15hhEKrXL1x-ZNZ1mrY1WUJH41OoIgD99USPxyEz03y4'
RANGE_NAME = 'Sheet1!A:A'  # Columns for Schedule Date, Lambda Function, Payload
AWS_REGION = os.getenv('AWS_REGION')
ACCOUNT_ID = os.getenv('ACCOUNT_ID')
AWS_ACCESS = os.getenv("AWS_ACCESS")
AWS_SECRET_ACCESS= os.getenv("AWS_SECRET_ACCESS")
sheet = service.spreadsheets()
result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME).execute()
rows = result.get('values', [])

# Initialize the boto3 client with explicit credentials
eventbridge = boto3.client(
    'events',
    aws_access_key_id=AWS_ACCESS,
    aws_secret_access_key=AWS_SECRET_ACCESS,
    region_name=os.getenv("AWS_REGION")
)
lambda_client = boto3.client(
    'lambda',
    aws_access_key_id=AWS_ACCESS,
    aws_secret_access_key=AWS_SECRET_ACCESS,
    region_name=os.getenv("AWS_REGION")
)


# Define the EventBridge schedule
schedule_name = 'FullTeamWeeklyReportReminder'
lambda_arn = 'arn:aws:lambda:us-east-1:891377090032:function:WeeklyReportReminder2'
role_arn = 'arn:aws:iam::891377090032:role/service-role/Amazon_EventBridge_Scheduler_LAMBDA_b5c5f3fb1b'

# Create the EventBridge schedule
def create_eventbridge_schedule(schedule_time, term, time_zone='America/New_York'):
    try:
        # Create the EventBridge rule
        cron_expr = convert_to_cron(schedule_time, time_zone)
        print(f"Creating cron expression for rule: {cron_expr}")
        if cron_expr is None:
            print(f"Error creating cron expression for rule: {schedule_time}")
            return
        scheduler_client.create_schedule(
            Name=schedule_name+str(term),
            ActionAfterCompletion='DELETE',
            ScheduleExpression=cron_expr,
            FlexibleTimeWindow={'Mode': 'OFF'},  # Set to OFF for exact time execution
            Target={
                'Arn': lambda_arn,
                'RoleArn': role_arn
            }
        )
        print(f"Successfully assigned Lambda function to schedule {schedule_name}")
        # print(f"Successfully assigned Lambda function {lambda_function_name} to schedule {schedule_name} with payload: {payload}")
    except Exception as e:
        print(f"Error creating EventBridge schedule {schedule_name}: {e}")


# Convert ISO 8601 date to cron format
def convert_to_cron(schedule_time, time_zone='America/New_York'):
    # Convert ISO 8601 date to cron format
    if ' ' in schedule_time:
        schedule_time = schedule_time.replace(' ', 'T')
    if '/' in schedule_time:
        schedule_time = schedule_time.replace('/', '-')
    try:
        dt_local = datetime.strptime(schedule_time, "%m-%d-%YT%H:%M:%S")
    except ValueError:
        print(f"Invalid date format: {schedule_time}")
        return None
    # Convert to local timezone
    local_tz = pytz.timezone(time_zone)
    dt_local = local_tz.localize(dt_local)
    dt_utc = dt_local.astimezone(pytz.utc)
    # Create a cron expression
    cron_expr = f'cron({dt_utc.minute} {dt_utc.hour} {dt_utc.day} {dt_utc.month} ? {dt_utc.year})'
    return cron_expr


# Process each row from the sheet
date_format = '%m-%d-%Y %H:%M:%S'
for i, row in enumerate(rows):
    schedule_time = row[0]
    # lambda_function_name = row[1]
    # payload = row[2]
    # print(f"Scheduling {lambda_function_name} at {schedule_time} with payload: {payload}")
    if '/' in schedule_time:
        schedule_time = schedule_time.replace('/', '-')
    # create_eventbridge_schedule(schedule_time, lambda_function_name, payload)
    term = i
    create_eventbridge_schedule(schedule_time, term)
