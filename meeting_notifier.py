import json
import os
import sys
import datetime
import time
import logging
import subprocess
from collections import defaultdict
import atexit
import uuid
import select
import termios
import tty

from googleapiclient.discovery import build
from datetime import datetime, timedelta, timezone
import pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.api_core.exceptions import AlreadyExists
from google.oauth2 import service_account
from google.cloud import pubsub_v1
from google.apps import meet_v2
from googleapiclient.errors import HttpError
from google.iam.v1 import policy_pb2


# Constants
TOPIC_ID = "meet-events"
PROJECT_ID = "meeting-notifier-412417"
ROOM_EMAIL = "c_188fmt6m2v6sahkjhd0kvdtkh12q6@resource.calendar.google.com"
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

from google.auth.exceptions import RefreshError

def get_meet_creds(use_sa=False, organizer=None):
    if use_sa:
        creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
        return creds.with_subject(organizer) if organizer else creds

    def oauth_flow():
        logger.info("Starting browser-based OAuth2 flow...")
        flow = InstalledAppFlow.from_client_secrets_file(OAUTH2_CREDENTIALS_FILENAME, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(OAUTH2_TOKEN_FILENAME, 'w') as token:
            token.write(creds.to_json())
        logger.info("Saved new OAuth2 token to %s", OAUTH2_TOKEN_FILENAME)
        return creds

    try:
        if os.path.exists(OAUTH2_TOKEN_FILENAME):
            creds = Credentials.from_authorized_user_file(OAUTH2_TOKEN_FILENAME, SCOPES)
            # Attempt an early refresh to verify scopes
            creds.refresh(Request())
            return creds
        else:
            return oauth_flow()

    except RefreshError as e:
        logger.warning("OAuth2 token refresh failed. Re-authenticating via browser...")
        if os.path.exists(OAUTH2_TOKEN_FILENAME):
            os.remove(OAUTH2_TOKEN_FILENAME)
        return oauth_flow()

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
    def active(self):
        return self.started() and not self.ended()
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
    except Exception as e:
        publisher.create_topic(request={"name": topic_path})
        logger.info(f"Created topic: {topic_path}")

    # Check IAM policy
    policy = publisher.get_iam_policy(request={"resource": topic_path})
    role = "roles/pubsub.publisher"
    member = "serviceAccount:meet-api-event-push@system.gserviceaccount.com"

    already_bound = any(
        b.role == role and member in b.members for b in policy.bindings
    )

    if not already_bound:
        policy.bindings.append(policy_pb2.Binding(role=role, members=[member]))
        publisher.set_iam_policy(request={"resource": topic_path, "policy": policy})
        logger.info("Granted meet-api-event-push permission on topic")

    return topic_path


def subscribe_to_meeting_space(meet_creds, space_id, topic_path):
    workspace_service = build('workspaceevents', 'v1', credentials=meet_creds)
    sub_name = f"subscriptions/meet-sub-{space_id.replace('/', '-')}"
    body = {
        "name": sub_name,
        "targetResource": f"//meet.googleapis.com/{space_id}",
        "eventTypes": [
            "google.workspace.meet.participant.v2.joined",
            "google.workspace.meet.participant.v2.left",
        ],
        "notification_endpoint": {
            "pubsub_topic": topic_path
        }
    }
    try:
        sub = workspace_service.subscriptions().create(body=body).execute()
        logger.info("Created Workspace Events subscription: %s", sub["name"])
        return sub["name"]
    except HttpError as e:
        if e.resp.status == 409 and "SUBSCRIPTION_ALREADY_EXISTS" in str(e):
            logger.info("Workspace Events subscription already exists: %s", sub_name)
            return sub_name
        logger.warning("Error creating Workspace Events subscription: %s", e)
        return None

def print_workspace_event_subscriptions(creds):
    logger.info("Listing current Workspace Events API subscriptions...")
    service = build("workspaceevents", "v1", credentials=creds)

    try:
        response = service.subscriptions().list().execute()
        subs = response.get("subscriptions", [])
        if not subs:
            logger.info("No active Workspace Events subscriptions found.")
            return

        for sub in subs:
            name = sub.get("name")
            target = sub.get("targetResource")
            event_types = sub.get("eventTypes", [])
            topic = sub.get("notificationEndpoint", {}).get("pubsubTopic")
            include_resource = sub.get("payloadOptions", {}).get("includeResource", False)

            logger.info("Subscription: %s", name)
            logger.info("  Target: %s", target)
            logger.info("  Topic: %s", topic)
            logger.info("  Event Types: %s", event_types)
            logger.info("  include_resource: %s", include_resource)

    except Exception as e:
        logger.error("Failed to list subscriptions: %s", e)


def start_pubsub_listener(subscription_path, meetings):
    sa_creds = service_account.Credentials.from_service_account_file(SA_FILE)
    logging.debug("sa_creds.email = %s",sa_creds.service_account_email)
    subscriber = pubsub_v1.SubscriberClient(credentials=sa_creds)

    def callback(message):
        try:
            data = json.loads(message.data.decode("utf-8"))
            logger.info(f"Received event: {json.dumps(data, indent=2)}")
            logger.debug(f"Full event payload: {json.dumps(data, indent=2)}")
            space_id = data.get("space", "")
            participant = data.get("participant", {})
            participant_email = participant.get("emailAddress", "")
            event_type = data.get("eventType", "")

            # Mark as joined if room joined
            if (event_type.endswith("joined") and
                space_id in meetings and
                participant_email.lower() == ROOM_EMAIL.lower()):
                logger.info(f"✅ Room joined meeting: {space_id}")
                meetings[space_id]["joined"] = True

            message.ack()
        except Exception as e:
            logger.error(f"PubSub message handling error: {e}")
            message.nack()

    subscription_path = f"projects/{PROJECT_ID}/subscriptions/{TOPIC_ID}-sub"
    try:
        subscriber.create_subscription(name=subscription_path, topic=topic_path)
    except AlreadyExists:
        logger.info("Pub/Sub subscription already exists")
    logger.info(f"Subscribing to Pub/Sub on {subscription_path}")
    subscriber.subscribe(subscription_path, callback=callback)


from google.auth.transport.requests import AuthorizedSession

def play_alert():
    logger.warning("⚠️ Conference room has not joined a live meeting!")
    subprocess.call(["afplay", MP3_FILE])  # macOS only; use mpg123 or aplay on Linux

def create_topic_and_configure(unique_topic_id):
    sa_creds = service_account.Credentials.from_service_account_file(SA_FILE)
    publisher = pubsub_v1.PublisherClient(credentials=sa_creds)
    topic_path = publisher.topic_path(PROJECT_ID, unique_topic_id)
    try:
        publisher.create_topic(request={"name": topic_path})
        logger.info(f"Created topic: {topic_path}")
    except Exception as e:
        logger.error(f"Error creating topic: {e}")
        raise
    # Set IAM policy
    policy = publisher.get_iam_policy(request={"resource": topic_path})
    role = "roles/pubsub.publisher"
    member = "serviceAccount:meet-api-event-push@system.gserviceaccount.com"
    already_bound = any(
        b.role == role and member in b.members for b in policy.bindings
    )
    if not already_bound:
        policy.bindings.append(policy_pb2.Binding(role=role, members=[member]))
        publisher.set_iam_policy(request={"resource": topic_path, "policy": policy})
        logger.info("Granted meet-api-event-push permission on topic")
    return topic_path, publisher

def delete_topic_on_exit(topic_path, publisher):
    def _delete():
        try:
            publisher.delete_topic(request={"topic": topic_path})
            logger.info(f"Deleted topic on exit: {topic_path}")
        except Exception as e:
            logger.warning(f"Failed to delete topic on exit: {e}")
    return _delete

def key_pressed():
    dr, dw, de = select.select([sys.stdin], [], [], 0)
    if dr:
        return sys.stdin.read(1)
    return None

def do_work(meetings, config, calendar_creds, args, topic_path):
    logger.info("loop again")
    events = get_todays_meetings(calendar_creds, config['monitor_calendar_id'])
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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

    # Remove inactive meetings
    inactive = set(meetings.keys()) - set(active)
    for sid in inactive:
        logger.debug(f"Removing expired meeting: {sid}")
        meetings.pop(sid)

    # Detect live meetings without room joined
    for sid, meta in meetings.items():
        if meta['start'] < now < meta['end'] and not meta['joined']:
            play_alert()

def main_loop(meetings, config, calendar_creds, args, topic_path):
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            do_work(meetings, config, calendar_creds, args, topic_path)
            print('\r\033[7mtype q to exit\033[0m', end='', flush=True)
            for _ in range(5):
                key = key_pressed()
                if key and key.lower() == 'q':
                    print("\nExiting on 'q' keypress.")
                    return
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting on keyboard interrupt.")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

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

    meet_creds = get_meet_creds(args.sa_creds)
    meetings = defaultdict(dict)

    topic_suffix = datetime.now().strftime("%Y%m%d%H%M%S")
    unique_topic_id = f"meet-events-{topic_suffix}"

    topic_path, publisher = create_topic_and_configure(unique_topic_id)
    atexit.register(delete_topic_on_exit(topic_path, publisher))

    subscription_path = f"projects/{PROJECT_ID}/subscriptions/{unique_topic_id}-sub"
    start_pubsub_listener(subscription_path, meetings)
    calendar_creds = service_account.Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)

    main_loop(meetings, config, calendar_creds, args, topic_path)
