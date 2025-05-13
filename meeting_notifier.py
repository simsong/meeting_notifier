import os
import sys
import signal
import time
import uuid
import threading
import argparse
import json

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.cloud import pubsub_v1
from google.oauth2 import service_account
from google.apps import meet_v2

SCOPES = [
    'https://www.googleapis.com/auth/meetings.space.readonly',
    # 'https://www.googleapis.com/auth/meetings.space.created', # Not strictly needed for subscribing
    'https://www.googleapis.com/auth/pubsub',
    'https://www.googleapis.com/auth/workspace.events' # Add the workspace events scope
]

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "meeting-notifier-412417")
SA_FILE = "service_account.json"
DEFAULT_TOPIC_ID = "meet-events-default"
SUBSCRIPTION_ID_PREFIX = "meet-events-sub"

# Globals for cleanup
subscription_path = None
subscriber = None

def authenticate():
    """Authenticates and returns Google API credentials."""
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
    """Retrieves the Google Meet space name (ID) using the meeting code."""
    client = meet_v2.SpacesServiceClient(credentials=creds)
    try:
        space = client.get_space(name=f"spaces/{meeting_code}")
        print(f"Full Resource Name (Meet ID): {space.name}")
        return space.name
    except Exception as e:
        print(f"Error retrieving space: {e}")
        return None

def subscribe_to_meet_events(creds, meet_id, pubsub_topic):
    """Subscribes to Google Meet events for the given meeting ID."""
    service = build('workspaceevents', 'v1', credentials=creds) # Corrected API name
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
            "include_resource": True # Changed to True to get participant details
        }
    }
    try:
        sub = service.subscriptions().create(body=body).execute()
        print(f"Created Meet events subscription: {sub['name']}")
        return sub['name']
    except Exception as e:
        if e.resp.status == 409 and "ALREADY_EXISTS" in str(e):
            print(f"Workspace Events subscription already exists for this meeting: //meet.googleapis.com/{meet_id}")
            project_id = PROJECT_ID
            subscription_name = f"projects/{project_id}/subscriptions/meet-events-sub-{meet_id.split('/')[-1]}"
            print(f"Using existing subscription name: {subscription_name}")
            return subscription_name
        else:
            print(f"Error creating Workspace Events subscription: {e}")
            return None

def listen_to_pubsub(subscription_path, creds):
    """Listens for messages on the Pub/Sub subscription and processes Meet events."""
    credentials = service_account.Credentials.from_service_account_file(SA_FILE)
    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)

    def callback(message):
        """Callback function to handle received Pub/Sub messages."""
        event_data = json.loads(message.data.decode('utf-8'))
        print(f"Received raw event:\n{json.dumps(event_data, indent=2)}\n")

        if "participant" in event_data and "eventType" in event_data:  # Check for participant details
            event_type = event_data["eventType"]
            participant = event_data["participant"]
            display_name = participant.get("displayName", "N/A")
            participant_id = participant.get("id", "N/A")
            if event_type == "google.workspace.meet.participant.v2.joined":
                print(f"Participant Joined: {display_name} ({participant_id})")
            elif event_type == "google.workspace.meet.participant.v2.left":
                print(f"Participant Left: {display_name} ({participant_id})")
        elif "conferenceRecord" in event_data:
            conference_record_name = event_data["conferenceRecord"]["name"]
            event_type = event_data.get("eventType")
            if event_type == "google.workspace.meet.conference.v2.started":
                print(f"Conference Started: {conference_record_name}")
            elif event_type == "google.workspace.meet.conference.v2.ended":
                print(f"Conference Ended: {conference_record_name}")
        else:
            print("Unknown event type")

        message.ack()

    future = subscriber.subscribe(subscription_path, callback=callback)
    print(f"Listening for Meet events on {subscription_path}...\n")
    try:
        future.result()
    except KeyboardInterrupt:
        print("Shutting down listener...")
        future.cancel()
        subscriber.close() # Close the subscriber.

def main():
    """Main function to orchestrate the Meet event listening process."""
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
            listen_to_pubsub(subscription_name, creds)  # Pass creds here
        else:
            print("Failed to create Workspace Events subscription.")
    else:
        print("Failed to create Pub/Sub subscription.")
    print("Exiting Main")

if __name__ == "__main__":
    main()
