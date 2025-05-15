from google.cloud import pubsub_v1

PROJECT_ID = "meeting-notifier-412417"

subscriber = pubsub_v1.SubscriberClient()
subs = subscriber.list_subscriptions(request={"project": f"projects/{PROJECT_ID}"})

for sub in subs:
    if sub.topic.endswith("_deleted-topic_"):
        print(f"Deleting orphaned subscription: {sub.name}")
        subscriber.delete_subscription(request={"subscription": sub.name})
