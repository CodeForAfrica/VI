import os
import mediacloud.api
from datetime import date # Import date

# Get the API key from the environment
api_key = os.getenv('MEDIACLOUD_API_KEY')

if not api_key:
    print("ERROR: MEDIACLOUD_API_KEY environment variable not set.")
else:
    print("API Key found in environment.")
    try:
        # Initialize the API client
        mc = mediacloud.api.SearchApi(api_key)
        print("MediaCloud API client initialized successfully.")

        # Perform a simple test query
        # Use a very basic query and a known public collection ID (like 1 for English Web)
        query = "test"  # Simple query
        start_date = date(2026, 3, 1)
        end_date = date(2026, 3, 10)   # Use date object
        collection_ids = [1]      # Public collection ID for testing

        print(f"Attempting test query: '{query}' in collection {collection_ids} from {start_date} to {end_date}...")
        
        # Call the API - PASS DATE OBJECTS
        result = mc.story_list(
            query=query,
            start_date=start_date, # Pass date object
            end_date=end_date,     # Pass date object
            collection_ids=collection_ids
        )

        print(f"Type of result: {type(result)}")
        print(f"Length of result (if applicable): {len(result) if hasattr(result, '__len__') else 'N/A'}")
        
        # If result is a tuple (stories, count), check the stories part
        if isinstance(result, tuple) and len(result) == 2:
            stories, count = result
            print(f"Type of stories list: {type(stories)}")
            print(f"Number of stories returned: {len(stories) if hasattr(stories, '__len__') else 'N/A'}")
            if stories and len(stories) > 0:
                print(f"First story keys: {list(stories[0].keys()) if isinstance(stories[0], dict) else 'Not a dict'}")
        elif hasattr(result, '__iter__'): # If it's a list or other iterable
            print(f"Result seems to be an iterable.")
            if result:
                 print(f"First item type: {type(result[0])}")
                 if isinstance(result[0], dict):
                     print(f"First item keys: {list(result[0].keys())}")
        else:
            print(f"Result is neither a tuple nor an iterable list-like object.")

    except Exception as e:
        print(f"Error during API test: {e}")
        print(f"Exception type: {type(e).__name__}")
