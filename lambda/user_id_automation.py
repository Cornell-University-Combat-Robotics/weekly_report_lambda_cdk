import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import json
import csv
from io import StringIO

def get_channel_members(channel_id):
    client = WebClient(token='xoxb-786449602374-6482999081171-pPH2mDYChuC6HLV46GzTkOsd')
    
    try:
        response = client.conversations_members(channel=channel_id)
        if response['ok']:
            print(response['members'])
            return response['members']
        else:
            print(f"Failed to retrieve channel members: {response.get('error', 'Unknown error')}")
            return None
    except SlackApiError as e:
        print(f"An error occurred: {e.response['error']}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None
    
def get_user_info(user_id):
    client = WebClient(token='xoxb-786449602374-6482999081171-pPH2mDYChuC6HLV46GzTkOsd')
    
    try:
        response = client.users_info(user=user_id)
        if response['ok']:
            return response['user']
        else:
            print(f"Failed to retrieve user info: {response.get('error', 'Unknown error')}")
            return None
    except SlackApiError as e:
        print(f"An error occurred: {e.response['error']}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def extract_channel_user_data(channel_id):
    user_ids = get_channel_members(channel_id)
    if user_ids is None:
        return None

    user_data = []
    for user_id in user_ids:
        user_info = get_user_info(user_id)
        if user_info:
            profile = user_info.get('profile', {})
            user_data.append({
                'name': user_info.get('real_name', 'N/A'),
                'title': profile.get('title', 'N/A'),
                'id': user_id
            })
    return user_data

def create_csv(user_data):
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=['name', 'title', 'id'])
    writer.writeheader()
    for data in user_data:
        writer.writerow(data)
    return output.getvalue()

# local testing
#def save_csv_locally(csv_data, file_name):
#    with open(file_name, 'w') as file:
#        file.write(csv_data)

def upload_to_s3(csv_data, bucket_name, file_name):
    s3 = boto3.client('s3')
    s3.put_object(Body=csv_data, Bucket=bucket_name, Key=file_name)

def lambda_handler(event, context):
    channel_id = 'C03UYNDBUPQ'
    user_data = extract_channel_user_data(channel_id)
    csv_data = create_csv(user_data)

    bucket_name = 'your-s3-bucket-name'
    file_name = 'slack_users.csv'

    upload_to_s3(csv_data, bucket_name, file_name)
    
    return {
        'statusCode': 200,
        'body': 'User data successfully uploaded to S3'
    }



# def main():
#     channel_id = 'C03UYNDBUPQ'
#     user_data = extract_channel_user_data(channel_id)
#     for user in user_data:
#         print(f"User: {user['name']} ({user['id']}) - Title: {user['title']}")

#     # Create CSV from user data
#     csv_data = create_csv(user_data)

#     # Save CSV locally or upload to S3
#     save_choice = input("Do you want to save the CSV locally or upload to S3? (local/s3): ").strip().lower()

#     if save_choice == "local":
#         file_name = "slack_users.csv"
#         save_csv_locally(csv_data, file_name)
#         print(f"User data successfully saved locally as {file_name}")

#     elif save_choice == "s3":
#         bucket_name = input("Enter the S3 bucket name: ").strip()
#         file_name = "slack_users.csv"
#         upload_to_s3(csv_data, bucket_name, file_name)
#         print(f"User data successfully uploaded to S3 bucket '{bucket_name}' as {file_name}")

#     else:
#         print("Invalid choice. Please run the script again and choose 'local' or 's3'.")

# if __name__ == "__main__":
#     main()