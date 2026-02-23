#!/usr/bin/env python
import os
import sys

def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)
    
def lambda_handler(event, context):
    """AWS Lambda entry point to trigger Django management command"""
    try:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        from django.core.management import execute_from_command_line
        
        # This triggers your specific command
        execute_from_command_line(['manage.py', 'ingest_mediacloud']) 
        
        return {
            'statusCode': 200,
            'body': 'Ingest Mediacloud command executed successfully'
        }
    except Exception as e:
        print(f"Error executing command: {str(e)}")
        return {
            'statusCode': 500,
            'body': f"Error: {str(e)}"
        }
if __name__ == '__main__':
    main()
