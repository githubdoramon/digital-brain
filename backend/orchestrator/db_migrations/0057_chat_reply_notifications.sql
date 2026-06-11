INSERT INTO notification_subscriptions (
    user_email,
    notification_type,
    notification_channels
)
SELECT DISTINCT user_email,
       'chat-reply',
       ARRAY['push']::TEXT[]
FROM (
    SELECT user_email
    FROM user_devices
    WHERE expo_push_token IS NOT NULL
    UNION
    SELECT user_email
    FROM notification_subscriptions
    WHERE 'push' = ANY(notification_channels)
) push_users
WHERE user_email IS NOT NULL
ON CONFLICT (user_email, notification_type)
DO UPDATE SET
    notification_channels = (
        SELECT ARRAY(
            SELECT DISTINCT channel
            FROM unnest(notification_subscriptions.notification_channels || ARRAY['push']::TEXT[]) AS channel
            ORDER BY channel
        )
    ),
    updated_at = NOW()
WHERE NOT ('push' = ANY(notification_subscriptions.notification_channels));
