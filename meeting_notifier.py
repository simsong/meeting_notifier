"""
Based on Simson Garfinkel's gdrive program
"""

import json
import os
import os.path
import sys
import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
import googleapiclient.errors

BAMBOO_CALENDAR_ID='c_188fmt6m2v6sahkjhd0kvdtkh12q6@resource.calendar.google.com'
PRIMARY_CALENDAR_ID='primary'


# If modifying these scopes, delete the file token.json.
# https://developers.google.com/calendar/api/auth
CALENDAR_READ_SCOPES = ['https://www.googleapis.com/auth/calendar.readonly',
          'https://www.googleapis.com/auth/calendar.events.readonly',
          'https://www.googleapis.com/auth/calendar.settings.readonly']

OAUTH2_CREDENTIALS_FILENAME = 'meeting_notifier_credentials.json'
OAUTH2_TOKEN_FILENAME='token.json'

"""
To create an Oauth 2.0 application:
https://support.google.com/cloud/answer/6158849?hl=en
"""

def get_credentials(*, oauth2_credentials_filename, oauth2_token_filename, scopes):
    if not os.path.exists(oauth2_credentials_filename):
        raise RuntimeError("create and register this OAuth 2.0 application")

    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(oauth2_token_filename):
        creds = Credentials.from_authorized_user_file(oauth2_token_filename, scopes)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file( oauth2_credentials_filename, scopes)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(OAUTH2_TOKEN_FILENAME, 'w') as token:
            token.write(creds.to_json())
    return creds

if __name__ == '__main__':
    """Shows basic usage of the Drive v3 API.
    Prints the names and ids of the first 10 files the user has access to.
    """

    import argparse
    parser = argparse.ArgumentParser(description="Check calendar",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    args = parser.parse_args()
    creds = get_credentials(oauth2_credentials_filename=OAUTH2_CREDENTIALS_FILENAME,
                            oauth2_token_filename=OAUTH2_TOKEN_FILENAME,
                            scopes = CALENDAR_READ_SCOPES)

    calendar_service = build("calendar", "v3", credentials=creds)

    # Get upcoming events (next 60 minutes)
    # Now the correct way to get a date; the Google API wants an ISO format string with a timezone
    # if timeMin=timeMax, no values are returned.
    # if timeMin+1 = timeMax, only the current meeting is returned.
    t0 = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    t1 = (datetime.datetime.now(tz=datetime.timezone.utc)+datetime.timedelta(minutes=1)).isoformat()
    print(t0,'-',t1)
    #calendar = PRIMARY_CALENDAR_ID
    calendar = BAMBOO_CALENDAR_ID
    events = (
        calendar_service.events()
        .list(calendarId=calendar, timeMin=t0, timeMax=t1, maxResults=10, singleEvents=t0, orderBy='startTime')
        .execute()
    )

    if len(events['items'])>0:
        current_event = events['items'][0]
        print("current event:",json.dumps(current_event,indent=4))

        admin_service = build("admin", "reports_v1", credentials=creds)


        join_events = admin_service.activities().list(
            applicationName="meet",
            userKey="allUsers",
            # startTime=now.isoformat(timespec="seconds")[:-60],
            # endTime=now.isoformat(timespec="seconds")
            startTime=t0,
            endTime=t1
        ).execute().get("items", [])
        print("join_events:",join_events)

        # This didn't work


        join_events = admin_service.user_devices().chrome_os_meet_activity().list(
            userKey="allUsers", startTime=now.isoformat(timespec="seconds")[:-60], endTime=now.isoformat(timespec="seconds")
        ).execute().get("results", [])

        room_joined = False
        for event in join_events:
            if event["meetingId"] == meeting_id:
                room_joined = True
                break

        if room_joined:
            print("Room has joined the meeting!")
        else:
            print("Meeting is ongoing, but room hasn't joined yet.")
