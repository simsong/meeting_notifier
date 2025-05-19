"""
I have spent about 20 hours creating a program that does everything described below. It all works! However, the events that I am receiving look like this:

2025-05-15 21:38:36,179 - INFO - Received event: {
  "participantSession": {
    "name": "conferenceRecords/de0f04e5-04d5-4c57-8cd8-afe589a190aa/participants/105103249624144546282/participantSessions/389"
  }
}
2025-05-15 21:38:37,395 - INFO - loop again
2025-05-15 21:38:37,399 - INFO - file_cache is only supported with oauth2client<4.0.0
2025-05-15 21:38:39,994 - INFO - Received event: {
  "participantSession": {
    "name": "conferenceRecords/de0f04e5-04d5-4c57-8cd8-afe589a190aa/participants/105103249624144546282/participantSessions/389"
  }
}

There is no information about who is joining or leaving. Google's sample program here says that such details are provided:
https://developers.google.com/workspace/meet/api/guides/tutorial-events-python

However, Google's documentation here says that the details are not provided:
https://developers.google.com/workspace/events/guides/events-meet
"""



import json
import os
import sys
import datetime
import time
import logging
import subprocess
from collections import defaultdict

from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2 import service_account
from google.cloud import pubsub_v1
from google.apps import meet_v2
from googleapiclient.errors import HttpError
from google.api_core.exceptions import AlreadyExists
from google.iam.v1 import policy_pb2

# Constants
TOPIC_ID = "meet-events"
PROJECT_ID = "meeting-notifier-412417"
ROOM_EMAIL = "c_188fmt6m2v6sahkjhd0kvdtkh12q6@resource.calendar.google.com"
FILTER_EMAIL = 'simsong@basistech.comx'
MP3_FILE = "alert.mp3"
RETENTION_SECONDS = 60

# Logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Credentials and config
SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/meetings.space.readonly',
    'https://www.googleapis.com/auth/meetings.space.created',
    'https://www.googleapis.com/auth/pubsub'
]

SA_FILE = 'service_account.json'
OAUTH2_TOKEN_FILENAME = 'meeting_notifier_continuous_token.json'
OAUTH2_CREDENTIALS_FILENAME = 'client_secrets.json'

def get_meet_creds(use_sa=False, organizer=None):
    if use_sa:
        creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
        return creds.with_subject(organizer) if organizer else creds
    if os.path.exists(OAUTH2_TOKEN_FILENAME):
        return Credentials.from_authorized_user_file(OAUTH2_TOKEN_FILENAME, SCOPES)
    flow = InstalledAppFlow.from_client_secrets_file(OAUTH2_CREDENTIALS_FILENAME, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(OAUTH2_TOKEN_FILENAME, 'w') as token:
        token.write(creds.to_json())
    return creds

class Event:
    def __init__(self, event):
        self.event = event
    def __repr__(self):
        return f"<Event {self.start} to {self.end} - {self.summary}>"
    @property
    def conferenceId(self):
        return self.event.get('conferenceData', {}).get('conferenceId')
    @property
    def start(self):
        return self.event['start'].get('dateTime')
    @property
    def end(self):
        return self.event['end'].get('dateTime')
    @property
    def ended(self):
        event_end = datetime.fromisoformat(self.end)
        if event_end.tzinfo is None:
            event_end = event_end.replace(tzinfo=timezone.utc)
        return event_end <= datetime.now(timezone.utc)
    @property
    def summary(self):
        return self.event.get('summary', '(No Title)')
    @property
    def organizer_email(self):
        return self.event.get('creator', {}).get('email')

def get_todays_meetings(creds, calendarId):
    tz = pytz.timezone('America/New_York')
    now = datetime.now(tz)
    time_min = tz.localize(datetime(now.year, now.month, now.day)).isoformat()
    time_max = (tz.localize(datetime(now.year, now.month, now.day)) + timedelta(days=1)).isoformat()

    calendar_service = build("calendar", "v3", credentials=creds)
    events_result = calendar_service.events().list(
        calendarId=calendarId,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    return [Event(e) for e in events_result.get('items', [])]

def ensure_topic_and_permissions():
    sa_creds = service_account.Credentials.from_service_account_file(SA_FILE)
    publisher = pubsub_v1.PublisherClient(credentials=sa_creds)
    topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)

    try:
        publisher.get_topic(request={"topic": topic_path})
        logger.info(f"Using existing topic: {topic_path}")
    except Exception:
        publisher.create_topic(request={"name": topic_path})
        logger.info(f"Created topic: {topic_path}")

    policy = publisher.get_iam_policy(request={"resource": topic_path})
    role = "roles/pubsub.publisher"
    member = "serviceAccount:meet-api-event-push@system.gserviceaccount.com"

    already_bound = any(b.role == role and member in b.members for b in policy.bindings)

    if not already_bound:
        policy.bindings.append(policy_pb2.Binding(role=role, members=[member]))
        publisher.set_iam_policy(request={"resource": topic_path, "policy": policy})
        logger.info("Granted meet-api-event-push permission on topic")

    return topic_path

def start_pubsub_listener(subscription_path, meetings):
    sa_creds = service_account.Credentials.from_service_account_file(SA_FILE)
    subscriber = pubsub_v1.SubscriberClient(credentials=sa_creds)

    def callback(message):
        try:
            data = json.loads(message.data.decode("utf-8"))
            logger.info(f"Received event: {json.dumps(data, indent=2)}")
            space_id = data.get("space", "")
            participant = data.get("participant", {})
            participant_email = participant.get("emailAddress", "")
            event_type = data.get("eventType", "")

            if (event_type.endswith("joined") and space_id in meetings and participant_email.lower() == ROOM_EMAIL.lower()):
                logger.info(f"✅ Room joined meeting: {space_id}")
                meetings[space_id]["joined"] = True

            message.ack()
        except Exception as e:
            logger.error(f"PubSub message handling error: {e}")
            message.nack()

    subscriber.subscribe(subscription_path, callback=callback)
    logger.info(f"Subscribing to Pub/Sub on {subscription_path}")

def subscribe_to_meeting_space(meet_creds, space_id, topic_path):
    workspace_service = build('workspaceevents', 'v1', credentials=meet_creds)
    body = {
        "targetResource": f"//meet.googleapis.com/{space_id}",
        "eventTypes": [
            "google.workspace.meet.participant.v2.joined",
            "google.workspace.meet.participant.v2.left",
            "google.workspace.meet.conference.v2.started",
            "google.workspace.meet.conference.v2.ended",
        ],
        "payloadOptions": {
            "includeResource": True,
        },
        "notificationEndpoint": {
            "pubsub_topic": topic_path
        },
        "ttl" : "86400s",
    }
    try:
        sub = workspace_service.subscriptions().create(body=body).execute()
        logger.info(f"Created subscription: {sub['name']}")
        return sub['name']
    except Exception as e:
        logger.warning(f"Error creating subscription for {space_id}: {e}")
        return None

def play_alert():
    logger.warning("\u26a0\ufe0f Conference room has not joined a live meeting!")
    subprocess.call(["afplay", MP3_FILE])

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--sa_creds", action="store_true")
    args = parser.parse_args()
    if args.debug:
        logger.setLevel(logging.DEBUG)

    with open("notifier_config.json") as f:
        config = json.load(f)

    topic_path = ensure_topic_and_permissions()
    subscription_path = f"projects/{PROJECT_ID}/subscriptions/{TOPIC_ID}-sub"
    meetings = defaultdict(dict)
    start_pubsub_listener(subscription_path, meetings)

    calendar_creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)

    while True:
        logger.info("loop again")
        events = get_todays_meetings(calendar_creds, config['monitor_calendar_id'])
        now = datetime.utcnow().isoformat() + "Z"
        active = []

        for e in events:
            if not e.conferenceId:
                logger.debug("EVENT: %s no conferenceId", e)
                continue
            if e.ended:
                logger.debug("EVENT: %s has already ended", e)
                continue
            meet_creds = get_meet_creds(args.sa_creds, e.organizer_email if args.sa_creds else None)
            meet_client = meet_v2.SpacesServiceClient(credentials=meet_creds)
            try:
                space = meet_client.get_space(name=f"spaces/{e.conferenceId}")
                space_id = space.name
                logger.debug("EVENT: %s space_id: %s", e, space_id)
                meetings[space_id].update({
                    'start': e.start,
                    'end': e.end,
                    'joined': False,
                    'summary': e.summary
                })
                if 'subscription' not in meetings[space_id]:
                    meetings[space_id]['subscription'] = subscribe_to_meeting_space(meet_creds, space_id, topic_path)
                active.append(space_id)
            except Exception as err:
                logger.error(f"Error retrieving space for {e.conferenceId}: {err}")

        inactive = set(meetings.keys()) - set(active)
        for sid in inactive:
            logger.debug(f"Removing expired meeting: {sid}")
            meetings.pop(sid)

        for sid, meta in meetings.items():
            if meta['start'] < now < meta['end'] and not meta['joined']:
                play_alert()

        time.sleep(5)
