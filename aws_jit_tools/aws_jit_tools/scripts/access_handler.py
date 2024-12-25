import logging
import os
import sys
import json
from typing import Optional, Dict, Any, List
from pathlib import Path
import time
import threading

# Add the parent directory to Python path to allow direct imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

try:
    import boto3
except ImportError as e:
    logger.error(f"Failed to import boto3: {str(e)}")
    print(json.dumps({
        "status": "error",
        "error_type": "ImportError",
        "message": "Required package boto3 is not installed - please ignore this during discovery"
    }))
    pass

try:
    from jinja2 import Template, Environment, FileSystemLoader
except ImportError as e:
    logger.error(f"Failed to import jinja2: {str(e)}")
    print(json.dumps({
        "status": "error",
        "error_type": "ImportError",
        "message": "Required package jinja2 is not installed - please ignore this during discovery"
    }))
    pass

try:
    import argparse
except ImportError as e:
    logger.error(f"Failed to import argparse: {str(e)}")
    print(json.dumps({
        "status": "error",
        "error_type": "ImportError",
        "message": "Required package argparse is not installed - please ignore this during discovery"
    }))
    pass

# Direct imports from the scripts directory
from scripts.utils.notifications import NotificationManager
from scripts.utils.aws_utils import get_account_alias, get_permission_set_details
from scripts.utils.slack_messages import create_access_revoked_blocks
from scripts.utils.webhook_handler import WebhookHandler

def print_progress(message: str, emoji: str) -> None:
    """Print progress messages with emoji."""
    print(f"\n{emoji} {message}", flush=True)
    sys.stdout.flush()

def format_duration(seconds: int) -> str:
    """Format duration in seconds to a human-readable string."""
    if seconds >= 3600:
        hours = seconds / 3600
        return f"{hours:.1f} hours"
    elif seconds >= 60:
        minutes = seconds / 60
        return f"{int(minutes)} minutes"
    else:
        return f"{seconds} seconds"

class AWSAccessHandler:
    def __init__(self, profile_name: Optional[str] = None):
        """Initialize AWS access handler."""
        try:
            print_progress("Initializing AWS handler...", "🔄")
            self.session = boto3.Session(profile_name=profile_name)
            self.identitystore = self.session.client('identitystore')
            self.sso_admin = self.session.client('sso-admin')
            self.notifications = NotificationManager()
            self.webhook_handler = WebhookHandler()
            self.iam_client = self.session.client('iam')
            
            print_progress("Fetching SSO instance details...", "🔍")
            instances = self.sso_admin.list_instances()['Instances']
            if not instances:
                raise ValueError("No SSO instance found")
            self.instance_arn = instances[0]['InstanceArn']
            self.identity_store_id = instances[0]['IdentityStoreId']
            print_progress("AWS handler initialized successfully", "✅")
            
        except Exception as e:
            self._handle_error("Failed to initialize AWS handler", e)

    def parse_iso8601_duration(self, duration: str) -> int:
        """Convert ISO8601 duration to seconds."""
        try:
            if duration.startswith('PT'):
                value = int(duration[2:-1])
                unit = duration[-1]
                if unit == 'H':
                    return value * 3600
                elif unit == 'M':
                    return value * 60
                elif unit == 'S':
                    return value
            return 3600  # Default 1 hour
        except Exception:
            return 3600

    def validate_duration(self, requested_duration: str, max_duration: str) -> str:
        """Validate that requested duration doesn't exceed maximum duration."""
        try:
            requested_seconds = self.parse_iso8601_duration(requested_duration)
            max_seconds = self.parse_iso8601_duration(max_duration)
            
            if requested_seconds > max_seconds:
                print_progress(f"Requested duration exceeds maximum allowed duration of {max_duration}. Using maximum duration.", "⚠️")
                return max_duration
            
            return requested_duration
        except Exception as e:
            print_progress(f"Invalid duration format. Using default duration of {max_duration}", "⚠️")
            return max_duration

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Find user by email in either IAM Identity Center or IAM."""
        try:
            print_progress(f"Looking up user: {email}", "👤")
            
            # First try IAM Identity Center if we have SSO configured
            if hasattr(self, 'identity_store_id'):
                try:
                    response = self.identitystore.list_users(
                        IdentityStoreId=self.identity_store_id,
                        Filters=[{
                            'AttributePath': 'UserName',
                            'AttributeValue': email
                        }]
                    )
                    users = response.get('Users', [])
                    if users:
                        print_progress(f"Found user in Identity Center: {users[0].get('UserName')}", "✅")
                        return users[0]
                except Exception as e:
                    logger.debug(f"Identity Center lookup failed: {e}")

            # If not found in Identity Center or if SSO is not configured, try IAM
            try:
                paginator = self.iam_client.get_paginator('list_users')
                for page in paginator.paginate():
                    for user in page['Users']:
                        if user['UserName'].lower() == email.lower():
                            print_progress(f"Found user in IAM: {user['UserName']}", "✅")
                            return user
                        
                        try:
                            tags_response = self.iam_client.list_user_tags(UserName=user['UserName'])
                            for tag in tags_response['Tags']:
                                if tag['Key'].lower() == 'email' and tag['Value'].lower() == email.lower():
                                    print_progress(f"Found user in IAM by email tag: {user['UserName']}", "✅")
                                    return user
                        except Exception as e:
                            logger.debug(f"Failed to get tags for user {user['UserName']}: {e}")
                            continue

            except Exception as e:
                logger.error(f"IAM user lookup failed: {e}")

            print_progress(f"No user found with email: {email}", "❌")
            return None

        except Exception as e:
            logger.error(f"Error finding user by email: {str(e)}")
            raise

    def get_permission_set_arn(self, permission_set_name: str) -> Optional[str]:
        """Get Permission Set ARN from its name."""
        try:
            print_progress(f"Looking up permission set: {permission_set_name}", "🔑")
            paginator = self.sso_admin.get_paginator('list_permission_sets')
            
            for page in paginator.paginate(InstanceArn=self.instance_arn):
                for permission_set_arn in page['PermissionSets']:
                    response = self.sso_admin.describe_permission_set(
                        InstanceArn=self.instance_arn,
                        PermissionSetArn=permission_set_arn
                    )
                    if response['PermissionSet']['Name'] == permission_set_name:
                        print_progress(f"Found permission set: {permission_set_name}", "✅")
                        return permission_set_arn
            
            print_progress(f"No permission set found with name: {permission_set_name}", "❌")
            return None

        except Exception as e:
            logger.error(f"Error finding permission set by name: {str(e)}")
            raise

    def grant_access(self, user_email: str, permission_set_name: str, requested_duration: str, max_duration: str):
        """Grant access for a user by email and permission set name."""
        try:
            print_progress(f"Granting access for {user_email} with permission set {permission_set_name}...", "🔄")
            
            # Validate duration
            validated_duration = self.validate_duration(requested_duration, max_duration)
            duration_seconds = self.parse_iso8601_duration(validated_duration)
            duration_display = format_duration(duration_seconds)
            
            # Find user by email
            user = self.get_user_by_email(user_email)
            if not user:
                raise ValueError(f"User not found: {user_email}")

            # Get permission set ARN
            permission_set_arn = self.get_permission_set_arn(permission_set_name)
            if not permission_set_arn:
                raise ValueError(f"Permission set not found: {permission_set_name}")
            
            # Get account alias and permission set details for better display
            account_alias = get_account_alias(self.session) or os.environ['AWS_ACCOUNT_ID']
            permission_set_details = get_permission_set_details(
                self.session, 
                self.instance_arn, 
                permission_set_arn
            )
            
            print_progress("Creating account assignment...", "⚙️")
            
            # Create assignment
            response = self.sso_admin.create_account_assignment(
                InstanceArn=self.instance_arn,
                TargetId=os.environ['AWS_ACCOUNT_ID'],
                TargetType='AWS_ACCOUNT',
                PermissionSetArn=permission_set_arn,
                PrincipalType='USER',
                PrincipalId=user['UserId']
            )

            # Print human-readable success message
            print_progress(f"Access granted successfully!", "✅")
            print(f"   ├─ Account: {account_alias} ({os.environ['AWS_ACCOUNT_ID']})")
            print(f"   ├─ User: {user_email}")
            print(f"   ├─ Permission Set: {permission_set_name}")
            print(f"   └─ Duration: {duration_display}")

            # Send notifications using the notification manager
            self.notifications.send_access_granted(
                account_id=os.environ['AWS_ACCOUNT_ID'],
                account_alias=account_alias,
                permission_set=permission_set_name,
                permission_set_details=permission_set_details,
                duration_seconds=duration_seconds,
                user_email=user_email
            )

            if os.environ.get('REVOKATION_WEBHOOK_URL'):
                self._schedule_revocation_webhook(
                    user_email=user_email,
                    duration_seconds=duration_seconds,
                    account_id=os.environ['AWS_ACCOUNT_ID'],
                    permission_set=permission_set_name,
                    policy_details={
                        "name": permission_set_name,
                        "type": "sso",
                        "details": permission_set_details
                    }
                )

        except Exception as e:
            self._handle_error("Failed to grant access", e)

    def grant_s3_access(self, user_email: str, bucket_name: str, policy_template: str, duration: str):
        """Grant S3 access to the user by updating the bucket policy."""
        try:
            print_progress(f"Granting S3 access for {user_email} to bucket {bucket_name}...", "🔄")
            
            # Parse duration and get formatted display
            duration_seconds = self.parse_iso8601_duration(duration)
            duration_display = format_duration(duration_seconds)
            
            # Find user by email
            user = self.get_user_by_email(user_email)
            if not user:
                raise ValueError(f"User not found: {user_email}")
            
            # Validate that the bucket exists
            s3 = self.session.client('s3')
            try:
                s3.head_bucket(Bucket=bucket_name)
            except s3.exceptions.NoSuchBucket:
                raise ValueError(f"S3 bucket {bucket_name} does not exist")

            # Update bucket policy
            success = self.update_bucket_policy(
                bucket_name=bucket_name,
                user_arn=user['Arn'],
                grant_access=True
            )
            if not success:
                raise ValueError("Failed to update bucket policy")

            # Schedule revocation webhook
            self._schedule_revocation_webhook(
                user_email=user_email,
                duration_seconds=duration_seconds,
                account_id=os.environ['AWS_ACCOUNT_ID'],
                buckets=[bucket_name],
                policy_details={
                    "name": bucket_name,
                    "type": "s3",
                    "template": policy_template,
                }
            )

            # Print success message
            print_progress(f"S3 access granted successfully!", "✅")
            print(f"   ├─ User: {user_email}")
            print(f"   ├─ Bucket: {bucket_name}")
            print(f"   └─ Duration: {duration_display}")

            # Send notifications
            self.notifications.send_s3_access_granted(
                account_id=os.environ['AWS_ACCOUNT_ID'],
                user_email=user_email,
                policy_template=policy_template,
                duration_seconds=duration_seconds,
                bucket_name=bucket_name
            )

        except Exception as e:
            self._handle_error("Failed to grant S3 access", e)

    def _schedule_revocation_webhook(self, 
                                   user_email: str,
                                   duration_seconds: int,
                                   account_id: str,
                                   permission_set: Optional[str] = None,
                                   policy_details: Optional[Dict[str, Any]] = None,
                                   buckets: Optional[list] = None):
        """Schedule the revocation webhook after the TTL expires."""
        def send_webhook():
            if not os.environ.get('REVOKATION_WEBHOOK_URL'):
                print("No revocation webhook URL configured, skipping webhook...")
                return
            
            time.sleep(duration_seconds)
            access_type = "s3" if buckets else "sso"
            self.webhook_handler.send_revocation_webhook(
                user_email=user_email,
                access_type=access_type,
                policy_details=policy_details or {},
                duration_seconds=duration_seconds,
                account_id=account_id,
                permission_set=permission_set,
                buckets=buckets
            )

        webhook_thread = threading.Thread(target=send_webhook)
        webhook_thread.daemon = True
        webhook_thread.start()

    def revoke_access(self, user_email: str, permission_set_name: str):
        """Revoke access for a user by email and permission set name."""
        try:
            print_progress(f"Revoking access for {user_email} with permission set {permission_set_name}...", "🔄")
            
            user = self.get_user_by_email(user_email
