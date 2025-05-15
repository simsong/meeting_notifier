meeting_notifier
===============
Alerts people in the conference room that they haven't joined the Google Meet.

This program watches the Google Calendar for the conference room and subscribes to the events for each meeting. If the meeting starts but the room hasn't joined, the program alerts the room's occupants by playing a tone on the speaker.

Inputs:
* Google Service Account (credentials in service_account.json)
* OAuth client token

Theory of operation

Every minute:
- Get a list of all today's meetings.
  - Build a datas structure with the start and end times of each of the room's meetings.
- Subscribe to events for every meeting to which we have not yet subscribed
- Unsubscribe to events that we are subscribed to that are not today.
- If the meeting is happening and the room is not in it, ring the bell.

Event processing:
- Display the event
- If the event is for this room, update if the room is in or out of the meeting


Setup
=====
- Create a service account
- Share the meeting room calendar with the service account

Either:
  1. Give the service account domain-wide-delegation
or:
  2. Authenticate as a user who has access.
