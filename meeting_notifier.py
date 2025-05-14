import json
import os
import os.path
import sys
import datetime
import time
import logging

from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2 import service_account
from google.apps import meet_v2
import googleapiclient.errors

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = 'America/New_York'  # adjust as needed

BAMBOO_CALENDAR_ID = 'c_188fmt6m2v6sahkjhd0kvdtkh12q6@resource.calendar.google.com'
SA_FILE = 'service_account.json'

SCOPES = [
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events.readonly',
    'https://www.googleapis.com/auth/calendar.settings.readonly',
    'https://www.googleapis.com/auth/meetings.space.readonly',
    'https://www.googleapis.com/auth/meetings.space.created',
    'https://www.googleapis.com/auth/admin.reports.audit.readonly'
]

SERVICE_ACCOUNT = 'service_account.json'
OAUTH2_CREDENTIALS_FILENAME = 'client_secrets.json'
OAUTH2_TOKEN_FILENAME = 'meeting_notifier_continuous_token.json'

POLLING_INTERVAL_SECONDS = 60
LOOKBACK_WINDOW_SECONDS = 3600  # Increased for broader testing
PROCESSED_EVENT_IDS = set()

def get_space_by_conferenceId(creds, conferenceId):
    """Retrieves the Google Meet space name (ID) using the meeting code."""
    client = meet_v2.SpacesServiceClient(credentials=creds)
    try:
        space = client.get_space(name=f"spaces/{conferenceId}")
        print(f"Full Resource Name (Meet ID): {space.name}")
        return space.name
    except Exception as e:
        print(f"Error retrieving space: {e}")
        return None

class Event:
    def __init__(self,event):
        """Create the event object from the dictonary."""
        self.event = event
    def __repr__(self):
        return f"<Event {self.start()} to {self.end()} - {self.summary()}"
    def conferenceId(self):
        return self.event['conferenceData']['conferenceId']
    def start(self):
        return self.event['start'].get('dateTime', self.event['start'].get('date'))
    def end(self):
        return self.event['end'].get('dateTime', self.event['end'].get('date'))
    def summary(self):
        return self.event.get('summary', '(No Title)')
    def started(self):
        datetime.fromisoformat(self.start()) <= datetime.now()
    def ended(self):
        datetime.fromisoformat(self.end()) <= datetime.now()
    def active(self):
        return self.started() and not self.ended()

def get_todays_meetings(creds, calendarId, timezone=DEFAULT_TIMEZONE):
    logger.debug("Fetching today's calendar events for %s",calendarId)
    calendar_service = build("calendar", "v3", credentials=creds)
    # Get start and end of today in local time
    tz = pytz.timezone(timezone)
    now = datetime.now(tz)
    start_of_day = tz.localize(datetime(now.year, now.month, now.day, 0, 0, 0))
    end_of_day = start_of_day + timedelta(days=1)

    # Convert to RFC3339 format
    time_min = start_of_day.isoformat()
    time_max = end_of_day.isoformat()

    # Call the API
    events_result = calendar_service.events().list(
        calendarId=calendarId,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])
    logger.debug("Events fetched: %s",len(events))
    return [Event(e) for e in events]

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Notify when the conference room has not joined the meeting",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args  = parser.parse_args()
    if args.debug:
        logger.setLevel(logging.DEBUG)
    with open("notifier_config.json","r") as f:
        config = json.load(f)
    creds = service_account.Credentials.from_service_account_file(SA_FILE)


    while True:
        events = get_todays_meetings(creds, config['monitor_calendar_id'])
        for e in events:
            print(e) # ,get_space_by_conferenceId(creds, e.conferenceId()))
        # Unsubscribe from every meeting that has been
        exit(0)

    print(f"Continuously monitoring for Google Meet events every {POLLING_INTERVAL_SECONDS} seconds...")

    try:
        while True:
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            start_time = (now - datetime.timedelta(seconds=LOOKBACK_WINDOW_SECONDS)).isoformat()
            end_time = now.isoformat()

            # Try fetching for all users
            fetch_and_print_meet_events(admin_service, start_time, end_time, user_key="allUsers")

            # Optionally, try fetching specifically for your user
            # Replace 'your_email@yourdomain.com' with your actual email
            # fetch_and_print_meet_events(admin_service, start_time, end_time, user_key="your_email@yourdomain.com")

            time.sleep(POLLING_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
