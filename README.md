meeting_notifier
===============
Alerts people in the conference room that they haven't joined the Google Meet.

This program watches the Google Calendar for the conference room and subscribes to the events for each meeting. If the meeting starts but the room hasn't joined, the program alerts the room's occupants by playing a tone on the speaker.

Inputs:
* Google Service Account (credentials in service_account.json)
* OAuth client token

Theory of operation
===================
- This application has a single pub/sub topic named meet-events
- The pub/sub topic is created by this script if it doesn't exist.

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




Setup
=====
- Create a service account
- Share the meeting room calendar with the service account

Either:
  1. Give the service account domain-wide-delegation
or:
  2. Authenticate as a user who has access.


Maintenance
==========