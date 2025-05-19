meeting_notifier
===============
Alerts people in the conference room that they haven't joined the Google Meet.

This program watches the Google Calendar for the conference room and subscribes to the events for each meeting. If the meeting starts but the room hasn't joined, the program alerts the room's occupants by playing a tone on the speaker.

Mandatory Inputs:
* Google Service Account (credentials in service_account.json)
  - The room's google calendar must have been shared with the service account.
* The CalendarID to monitor

One of these:
* Either the Google Service Account can pose as the meeting owner (really, everyone in the domain)
* Or OAuth authentication to a real user account. (This is great for testing, but the token needs to be periodically renewed, so it won't work for a deployment.)

Outputs:
* Currently, the program prints all meeting events on stdout.
* Unfortunately, currently there is no detail on the events to detemrine what is happening and why.

Theory of operation
===================
- This application creates a pub/sub topic
- The application subscribes to events for the meeting.
- The application prints events as they happen


Global data structure:
- Meetings that have not yet ended
  - defaultdict(dict) indexed by meeting space ID (not meeting code, which may be reused)
  - records meetings that have been joined by conference room

On startup:
- Verify that the pub/sub topic exists. If not, create it.
- Set the message retention and ack deadlines on subscriptions to 10 seconds.
- Register a listener for events from the topic.

For every event that arrives:
- If it is for a person or meeting room joining or leaving the conference room we are monitoring, print that.
- If the conference room has joined the meeting, note that in the list of meetings that have not yet ended.
- Does not record if the conference room leaves.

Every minute:
- Get a list of all today's meetings that have not ended.
  - Build a datas structure with the start and end times of each of the room's meetings.
  - Remove from the list all meetings that have not yet ended.
- Get a list of all current subscriptions to the topic.
  - Delete each unless in the list of meetings that have not yet ended.
- Create new subscriptions for every meeting in the list of meetings that have not yet ended for which there is not yet a subscription.
- If there is a meeting that has STARTED but has not ENDED and the conference room has not yet joined:
  - Print an alert
  - Play an MP3


Problems
==========
Currently, due to an apparent bug in Google Meet, not enough detail is printed on the events to know what is going on.

I have spent about 20 hours creating a program that does everything described below. It all works! However, the events that I am receiving look like this:

```
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
```

There is no information about who is joining or leaving. Google's sample program here says that such details are provided:
https://developers.google.com/workspace/meet/api/guides/tutorial-events-python

However, Google's documentation here says that the details are not provided:
https://developers.google.com/workspace/events/guides/events-meet
