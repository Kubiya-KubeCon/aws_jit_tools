from typing import Dict, Any, Optional
import json

def format_duration(seconds: int) -> str:
    """
    Format duration in seconds to a human-readable string
    """
    if seconds >= 3600:
        hours = seconds / 3600
        return f"{hours:.1f} hours"
    elif seconds >= 60:
        minutes = seconds / 60
        return f"{int(minutes)} minutes"
    else:
        return f"{seconds} seconds"

def create_access_granted_blocks(account_id: str, permission_set: str, duration_seconds: int, 
                               user_email: str, account_alias: Optional[str] = None,
                               permission_set_details: Optional[dict] = None) -> Dict[str, Any]:
    """Create engaging Slack Block Kit message for access grant notification."""
    
    duration_display = format_duration(duration_seconds)
    account_name = account_alias or account_id
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🎉 AWS Access Granted! 🎉",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"You've been granted access to AWS account *{account_name}* ({account_id}) with permission set *{permission_set}*"
            }
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Duration:*\n{duration_display}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*User:*\n{user_email}"
                }
            ]
        }
    ]

    # Add permission set details if available
    if permission_set_details:
        description = permission_set_details.get('Description', 'No description available')
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Permission Set Details:*\n{description}"
            }
        })

    # Add access instructions with more detailed steps
    blocks.extend([
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*How to Access AWS:*"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*🌐 Web Console Access*\n1. Visit: <https://signin.aws.amazon.com/switchrole?account={account_id}|AWS Console>\n2. Sign in with your SSO credentials\n3. Select account *{account_name}* ({account_id})\n4. You should now have access with the *{permission_set}* permission set"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*💻 AWS CLI Access*\n1. Configure AWS CLI SSO:\n```aws configure sso\nSSO start URL: https://YOUR_SSO_URL\nSSO Region: YOUR_SSO_REGION\nAccount ID: " + account_id + "\nRole name: " + permission_set + "\nCLI profile name: [choose-a-name]```\n2. Login and get credentials:\n```aws sso login```\n3. Test your access:\n```aws sts get-caller-identity```"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*🔧 AWS SDK Configuration (Python)*\n```python\nimport boto3\nsession = boto3.Session()\ns3 = session.client('s3')\n# The session will use your SSO credentials automatically```"
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"⏰ Access will expire in {duration_display}"
                }
            ]
        }
    ])

    return {"blocks": blocks}
