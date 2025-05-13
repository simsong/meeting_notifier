"""
This version prints people as they join and leave.
Problems:
- It leaves the pub/sub running and fails to run a second time.
- It doesn't print full names.
"""



import os
import sys
import signal
import time
import uuid
import threading
import argparse

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google.cloud import pubsub_v1
from google.oauth2 import service_account
from google.apps import meet_v2

SCOPES = [
    'https://www.googleapis.com/auth/meetings.space.readonly',
    # 'https://www.googleapis.com/auth/meetings.space.created', # Not strictly needed for subscribing
    'https://www.googleapis.com/auth/pubsub'
]

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "meeting-notifier-412417")
SA_FILE = "service_account.json"
DEFAULT_TOPIC_ID = "meet-events-default"
SUBSCRIPTION_ID_PREFIX = "meet-events-sub"

# Globals for cleanup
subscription_path = None
subscriber = None

def authenticate():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

def create_pubsub_subscription(project_id, topic_id):
    """Creates a unique Pub/Sub subscription for the given topic."""
    global subscriber, subscription_path
    credentials = service_account.Credentials.from_service_account_file(SA_FILE)
    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)
    subscription_id = f"{SUBSCRIPTION_ID_PREFIX}-{uuid.uuid4().hex[:8]}"
    topic_path = subscriber.topic_path(project_id, topic_id)
    subscription_path = subscriber.subscription_path(project_id, subscription_id)

    try:
        subscription = subscriber.create_subscription(
            request={"name": subscription_path, "topic": topic_path}
        )
        print(f"Created subscription: {subscription.name}")
        return subscription.name
    except Exception as e:
        if "ALREADY_EXISTS" in str(e):
            print(f"Subscription already exists: {subscription_path}")
            return subscription_path  # Return the constructed path
        else:
            print(f"Error creating subscription: {e}")
            return None


def get_space_by_meeting_code(creds, meeting_code):
    client = meet_v2.SpacesServiceClient(credentials=creds)
    try:
        space = client.get_space(name=f"spaces/{meeting_code}")
        print(f"Full Resource Name (Meet ID): {space.name}")
        return space.name
    except Exception as e:
        print(f"Error retrieving space: {e}")
        return None

def subscribe_to_meet_events(creds, meet_id, pubsub_topic):
    service = build('workspaceevents', 'v1', credentials=creds)
    body = {
        "targetResource": f"//meet.googleapis.com/{meet_id}",
        "eventTypes": [
            "google.workspace.meet.conference.v2.started",
            "google.workspace.meet.conference.v2.ended",
            "google.workspace.meet.participant.v2.joined",
            "google.workspace.meet.participant.v2.left"
        ],
        "notification_endpoint": {
            "pubsub_topic": pubsub_topic
        },
        "payload_options": {
            "include_resource": False
        }
    }
    try:
        sub = service.subscriptions().create(body=body).execute()
        print(f"Created Meet events subscription: {sub['name']}")
        return sub['name']
    except Exception as e:
        if e.resp.status == 409 and "ALREADY_EXISTS" in str(e):
            print(f"Workspace Events subscription already exists for this meeting: //meet.googleapis.com/{meet_id}")
            #  The error indicates that the subscription exists.  We should be able to construct the subscription name.
            project_id = PROJECT_ID
            subscription_name = f"projects/{project_id}/subscriptions/meet-events-sub-{meet_id.split('/')[-1]}"
            print(f"Using existing subscription name: {subscription_name}")
            return subscription_name
        else:
            print(f"Error creating Workspace Events subscription: {e}")
            return None

def listen_to_pubsub(subscription_path, creds):
    meet_service = build('meet', 'v1', credentials=creds)

    def callback(message):
        event_data = json.loads(message.data.decode('utf-8'))
        print(f"Received raw event:\n{json.dumps(event_data, indent=2)}\n")

        if "participantSession" in event_data:
            participant_session_name = event_data["participantSession"]["name"]
            participant_id = participant_session_name.split('/')[5]
            conference_record_id = participant_session_name.split('/')[1]
            try:
                participant = meet_service.participants().get(name=f"conferenceRecords/{conference_record_id}/participants/{participant_id}").execute()
                display_name = participant.get("displayName", "N/A")
                print(f"Participant Event: {display_name} ({participant_id})")
            except HttpError as e:
                print(f"Error getting participant info: {e}")

        elif "conferenceRecord" in event_data:
            conference_record_name = event_data["conferenceRecord"]["name"]
            print(f"Conference Event: {conference_record_name}")

        message.ack()

    credentials = service_account.Credentials.from_service_account_file(SA_FILE)
    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)

    future = subscriber.subscribe(subscription_path, callback=callback)
    print(f"Listening for Meet events on {subscription_path}...\n")
    try:
        future.result()
    except KeyboardInterrupt:
        print("Shutting down listener...")
        future.cancel()

def main():
    parser = argparse.ArgumentParser(description="Listen to Google Meet events.")
    parser.add_argument("meeting_code", help="The Google Meet space ID (e.g., xyj-dans-fkp)")
    args = parser.parse_args()

    creds = authenticate()
    meet_id = get_space_by_meeting_code(creds, args.meeting_code)

    if not meet_id:
        print("Could not retrieve Meet space ID. Exiting.")
        return

    topic_path = f"projects/{PROJECT_ID}/topics/{DEFAULT_TOPIC_ID}"
    subscription_name = create_pubsub_subscription(PROJECT_ID, DEFAULT_TOPIC_ID)

    if subscription_name:
        workspace_events_subscription = subscribe_to_meet_events(creds, meet_id, topic_path)
        if workspace_events_subscription:
            print(f"Workspace Events subscription created: {workspace_events_subscription}")
            listen_to_pubsub(subscription_name, creds) # Pass creds here
        else:
            print("Failed to create Workspace Events subscription.")
    else:
        print("Failed to create Pub/Sub subscription.")

if __name__ == "__main__":
    main()
