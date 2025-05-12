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

SCOPES = [
    'https://www.googleapis.com/auth/meetings.space.readonly',
    'https://www.googleapis.com/auth/meetings.space.created',
    'https://www.googleapis.com/auth/pubsub'
]

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    print("You must set the GOOGLE_CLOUD_PROJECT environment variable.")
    sys.exit(1)

# Globals for cleanup
topic_path = None
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

from google.oauth2 import service_account
from google.cloud import pubsub_v1

def create_pubsub(_):
    global topic_path, subscription_path, subscriber

    sa_creds = service_account.Credentials.from_service_account_file('service_account.json')

    publisher = pubsub_v1.PublisherClient(credentials=sa_creds)
    subscriber = pubsub_v1.SubscriberClient(credentials=sa_creds)

    topic_id = f"meet-events-{uuid.uuid4().hex[:8]}"
    sub_id = f"meet-events-sub-{uuid.uuid4().hex[:8]}"
    topic_path = publisher.topic_path(PROJECT_ID, topic_id)
    subscription_path = subscriber.subscription_path(PROJECT_ID, sub_id)

    publisher.create_topic(request={"name": topic_path})
    subscriber.create_subscription(name=subscription_path, topic=topic_path)
    print(f"Created topic: {topic_path}")
    print(f"Created subscription: {subscription_path}")
    return topic_path, subscription_path


def subscribe_to_meet_events(creds, meet_id, pubsub_topic):
    service = build('workspaceevents', 'v1', credentials=creds)
    body = {
        "targetResource": f"//meet.googleapis.com/spaces/{meet_id}",
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
    sub = service.subscriptions().create(body=body).execute()
    print(f"Created Meet events subscription: {sub['name']}")

def listen_to_pubsub(subscription_path):
    def callback(message):
        print(f"Received event:\n{message.data.decode('utf-8')}\n")
        message.ack()

    future = subscriber.subscribe(subscription_path, callback=callback)
    print(f"Listening for Meet events on {subscription_path}...\n")
    try:
        future.result()
    except KeyboardInterrupt:
        print("Shutting down listener...")
        future.cancel()

def cleanup():
    global topic_path, subscription_path, subscriber
    print("Cleaning up Pub/Sub resources...")
    if subscriber:
        try:
            if subscription_path:
                subscriber.delete_subscription(subscription=subscription_path)
                print(f"Deleted subscription: {subscription_path}")
            if topic_path:
                publisher = pubsub_v1.PublisherClient()
                publisher.delete_topic(topic=topic_path)
                print(f"Deleted topic: {topic_path}")
        except Exception as e:
            print(f"Cleanup error: {e}")


def list_active_spaces(creds):
    service = build('meet', 'v2', credentials=creds)
    spaces = service.spaces().list().execute().get('spaces', [])
    active_spaces = [space for space in spaces if 'activeConference' in space]
    for space in active_spaces:
        print(f"Space Name: {space['name']}")
        print(f"Meeting URI: {space.get('meetingUri')}")
        print(f"Meeting Code: {space.get('meetingCode')}")
        print("-----")


from google.oauth2 import service_account
from google.apps import meet_v2

def get_space_by_meeting_code(creds,meeting_code):
    client = meet_v2.SpacesServiceClient(credentials=creds)

    request = meet_v2.GetSpaceRequest(name=f"spaces/{meeting_code}")

    try:
        space = client.get_space(request=request)
        print(f"Full Resource Name: {space.name}")
        return space.name
    except Exception as e:
        print(f"Error retrieving space: {e}")
        return None

PROJECT_ID = "meeting-notifier-412417"
PREFIX = "meet-events"  # Only delete topics/subs that start with this
SA_FILE = "service_account.json"

def delete_pubsub_resources():
    credentials = service_account.Credentials.from_service_account_file(SA_FILE)

    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)
    publisher = pubsub_v1.PublisherClient(credentials=credentials)

    # Get all subscriptions
    print("Checking subscriptions...")
    subs = subscriber.list_subscriptions(request={"project": f"projects/{PROJECT_ID}"})
    for sub in subs:
        if PREFIX in sub.name:
            print(f"Deleting subscription: {sub.name}")
            subscriber.delete_subscription(request={"subscription": sub.name})

    # Get all topics
    print("Checking topics...")
    topics = publisher.list_topics(request={"project": f"projects/{PROJECT_ID}"})
    for topic in topics:
        print(f"Found topic: {topic.name}")
        if PREFIX in topic.name:
            print(f"Deleting topic: {topic.name}")
            publisher.delete_topic(request={"topic": topic.name})


def main():
    parser = argparse.ArgumentParser(description="Listen to Google Meet events.")
    parser.add_argument("meeting_code", help="The Google Meet space ID (not the URL)")
    args = parser.parse_args()

    creds = authenticate()

    if args.meeting_code=='list':
        list_active_spaces(creds)
        exit(0)

    if args.meeting_code=='del':
        delete_pubsub_resources()
        exit(0)

    meet_id = get_space_by_meeting_code(creds,args.meeting_code)

    topic, sub = create_pubsub(creds)
    subscribe_to_meet_events(creds, meet_id, topic)

    # Cleanup on Ctrl+C
    signal.signal(signal.SIGINT, lambda sig, frame: (cleanup(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda sig, frame: (cleanup(), sys.exit(0)))

    listen_to_pubsub(sub)

if __name__ == "__main__":
    main()
