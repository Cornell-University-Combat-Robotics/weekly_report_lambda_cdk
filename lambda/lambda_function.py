# lambda/lambda_function.py
import os
import http.client
import urllib.parse
import datetime
import boto3
from botocore.exceptions import ClientError

# Used only in reminder path; import here so Friday path works without these deps if not bundled
try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    import pytz
    _REMINDER_DEPS_AVAILABLE = True
except ImportError:
    _REMINDER_DEPS_AVAILABLE = False


def get_timestamps_two_days_prior():
    """Time window for the Friday thread: two days ago (EST) 3pm–11:45pm."""
    est = pytz.timezone('America/New_York')
    now_est = datetime.datetime.now(est)
    two_days_ago = now_est - datetime.timedelta(days=2)
    timestamp_start = two_days_ago.replace(hour=15, minute=0, second=0, microsecond=0)
    timestamp_end = two_days_ago.replace(hour=23, minute=45, second=0, microsecond=0)
    return timestamp_start.timestamp(), timestamp_end.timestamp()


def _fetch_channel_members(client, channel_id):
    """Fetch all member IDs in the channel (handles pagination)."""
    members = []
    cursor = None
    while True:
        kwargs = {'channel': channel_id}
        if cursor:
            kwargs['cursor'] = cursor
        response = client.conversations_members(**kwargs)
        members.extend(response.get('members', []))
        cursor = response.get('response_metadata', {}).get('next_cursor')
        if not cursor:
            break
    return set(members)


def _collect_repliers_with_files(client, channel_id, timestamp_start, timestamp_end):
    """Collect user IDs who replied with png/jpg/jpeg/pdf in threads in the time window. Uses pagination."""
    user_ids_already_replied = set()
    cursor = None
    while True:
        kwargs = {
            'channel': channel_id,
            'oldest': str(timestamp_start),
            'latest': str(timestamp_end),
        }
        if cursor:
            kwargs['cursor'] = cursor
        response = client.conversations_history(**kwargs)
        for message in response.get('messages', []):
            if message.get('reply_count', 0) > 0:
                thread_ts = message.get('thread_ts') or message['ts']
                reply_cursor = None
                while True:
                    reply_kwargs = {'channel': channel_id, 'ts': thread_ts}
                    if reply_cursor:
                        reply_kwargs['cursor'] = reply_cursor
                    thread_response = client.conversations_replies(**reply_kwargs)
                    for reply in thread_response.get('messages', [])[1:]:  # skip root message
                        if 'files' not in reply:
                            continue
                        for f in reply.get('files', []):
                            if f.get('filetype') in ['png', 'jpg', 'jpeg', 'pdf']:
                                uid = reply.get('user')
                                if uid:
                                    user_ids_already_replied.add(uid)
                                break
                    reply_cursor = thread_response.get('response_metadata', {}).get('next_cursor')
                    if not reply_cursor:
                        break
        cursor = response.get('response_metadata', {}).get('next_cursor')
        if not cursor:
            break
    return user_ids_already_replied


def run_reminder(slack_token, channel_id, all_user_ids=None):
    """Notify users who have not submitted (no image/pdf in Friday thread). If all_user_ids is None, use channel members."""
    if not _REMINDER_DEPS_AVAILABLE:
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': 'Reminder dependencies (slack_sdk, pytz) not installed. Add them to the Lambda package.',
        }
    client = WebClient(token=slack_token)
    if all_user_ids is None:
        all_user_ids = _fetch_channel_members(client, channel_id)
    else:
        all_user_ids = set(all_user_ids)
    timestamp_start, timestamp_end = get_timestamps_two_days_prior()
    user_ids_already_replied = _collect_repliers_with_files(
        client, channel_id, timestamp_start, timestamp_end
    )
    user_ids_to_notify = all_user_ids - user_ids_already_replied
    try:
        if user_ids_to_notify:
            user_mentions = ' '.join([f'<@{uid}>' for uid in user_ids_to_notify])
            message = f"{user_mentions}, folks please turn in your weekly report!"
            client.chat_postMessage(channel=channel_id, text=message)
        else:
            message = "Let's go! Seems that everyone has turned in the weekly report!"
            client.chat_postMessage(channel=channel_id, text=message)
        return {
            'statusCode': 200,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': 'Notifications sent successfully',
        }
    except SlackApiError as e:
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': str(e.response.get('error', e)),
        }


def lambda_handler(event, context):
    # Check password for all modes
    if event.get('password') != os.environ.get('SLACK_PASSWORD'):
        return {
            'statusCode': 401,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': 'Invalid password',
        }

    # Fine-grain reminder (last hour before deadline): only @ people who haven’t submitted image/pdf in Friday thread
    if event.get('reminder') or 'all_user_ids' in event:
        slack_token = os.environ.get('SLACK_TOKEN')
        # conversations_* APIs need channel ID; use SLACK_CHANNEL_ID if set, else SLACK_CHANNEL
        channel_id = os.environ.get('SLACK_CHANNEL_ID') or os.environ.get('SLACK_CHANNEL')
        if not slack_token or not channel_id:
            return {
                'statusCode': 500,
                'headers': {'Access-Control-Allow-Origin': '*'},
                'body': 'SLACK_TOKEN and SLACK_CHANNEL must be set for reminder mode',
            }
        # SLACK_CHANNEL must be the channel ID (e.g. C03UYNDBUPQ) for conversations_* APIs
        all_user_ids = event.get('all_user_ids')
        if all_user_ids is not None and not isinstance(all_user_ids, set):
            all_user_ids = set(all_user_ids) if all_user_ids else None
        return run_reminder(slack_token, channel_id, all_user_ids)

    # Normal mode: post one message (Friday thread starter or generic “due tonight” reminder). Uses DynamoDB week_num.
    dynamodb = boto3.resource('dynamodb')
    table_name = os.environ.get('TABLE_NAME')
    table = dynamodb.Table(table_name)
    key_to_retrieve = {'sem': 'Fa24'}

    try:
        response = table.get_item(Key=key_to_retrieve)
        if 'Item' in response:
            week_num = int(response['Item']['week_num']) + event.get('increment', 0)
            table.update_item(
                Key=key_to_retrieve,
                UpdateExpression='SET week_num = :val',
                ExpressionAttributeValues={':val': week_num},
            )
        else:
            week_num = 1
            table.put_item(Item={'sem': 'Fa24', 'week_num': week_num})
    except ClientError as e:
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': f"Error accessing DynamoDB: {e.response['Error']['Message']}",
        }

    combined_text = f"WEEK {week_num} {event['data']}"
    params = urllib.parse.urlencode({
        'channel': os.environ.get('SLACK_CHANNEL'),
        'text': combined_text,
    })
    headers = {
        'Authorization': f"Bearer {os.environ.get('SLACK_TOKEN')}",
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    conn = http.client.HTTPSConnection("slack.com")
    conn.request("POST", "/api/chat.postMessage", params, headers)
    response = conn.getresponse()
    data = response.read().decode()
    conn.close()

    if response.status == 200:
        return {
            'statusCode': 200,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': data,
        }
    return {
        'statusCode': response.status,
        'headers': {'Access-Control-Allow-Origin': '*'},
        'body': f"Error: {data}",
    }
