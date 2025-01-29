from datetime import datetime
import os
import re
import json
import re
import pandas as pd
import requests
import time
import logging
import azure.cognitiveservices.speech as speechsdk
from openai import AzureOpenAI
from langchain_community.document_loaders import UnstructuredExcelLoader
from deep_translator import GoogleTranslator
from config import (
    SPEECH_KEY, SPEECH_REGION, AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class VirtualAssistant:
    def __init__(self):
        try:
            # Initialize Azure OpenAI client
            self.client = AzureOpenAI(
                azure_endpoint=AZURE_OPENAI_ENDPOINT,
                api_key=AZURE_OPENAI_API_KEY,
                api_version="2024-05-01-preview"
            )
            
            # Initialize speech configuration
            self.speech_config = speechsdk.SpeechConfig(
                subscription=SPEECH_KEY,
                region=SPEECH_REGION
            )
            
            # Configure audio
            self.audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
            self.audio_output_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
            
            # Set default language
            self.current_language = "en-US"
            self.speech_config.speech_recognition_language = "en-US"
            self.speech_config.speech_synthesis_voice_name = 'en-US-AvaNeural'
            
            # Initialize language selected flag
            self.language_selected = False
            # Your existing initialization code...
            self.initialize_complaints_file()
            self.initialize_order_status_file()
            self.conversation_state = {
            'pending_order_id': None,
            'pending_action': None,
            'last_topic': None
            }

            # Initialize conversation thread
            self.conversation_thread = None
            self._last_topic = None  # Initialize _last_topic here
            # Initialize speech services
            self._init_speech_services()
            
            # Initialize the assistant after data is loaded
            self.assistant = self.create_assistant()
            
            logger.info("Virtual Assistant initialized successfully")
            
        except Exception as e:
            logger.error(f"Error initializing Virtual Assistant: {str(e)}")
            raise

        # Get all files
        files = self.get_existing_files()
        if files:
            print("\nAvailable files:")
            for file in files:
                print(f"\nFile ID: {file['id']}")
                print(f"Status: {file.get('status', 'Unknown')}")
                if file.get('created_at'):
                    created_time = datetime.fromtimestamp(file['created_at'])
                    print(f"Created: {created_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"Size: {file.get('usage_bytes', 'Unknown')} bytes")
                print(f"Vector Store: {file['vector_store_id']}")

        # Find specific file
        order_status_id = self.find_order_status_file()
        if order_status_id:
            print(f"Found Order Status file ID: {order_status_id}")

    def sync_vector_store(self, data, data_type="order_status"):
        """Sync local data with vector store"""
        try:
            vector_store_id = self.assistant.tool_resources.file_search.vector_store_ids[0]
            logger.info(f"Syncing {data_type} data with vector store {vector_store_id}")

            try:
                # Create new file in files API
                response = self.client.beta.files.create(
                    file=json.dumps(data, indent=2).encode(),
                    purpose="assistants"
                )
                
                # Attach file to assistant
                self.client.beta.assistants.files.create(
                    assistant_id=self.assistant.id,
                    file_id=response.id
                )
                
                logger.info(f"Successfully synced {data_type} data with vector store")
                return True
                
            except Exception as e:
                logger.error(f"Vector store sync failed: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"Error in vector store sync: {str(e)}")
            return False
        
    def initialize_order_status_file(self):
        """Initialize order_status.json if needed"""
        try:
            try:
                with open('order_status.json', 'r') as file:
                    json.load(file)  # Validate JSON format
                    logger.info("Order status file exists and is valid")
            except FileNotFoundError:
                # Create initial empty orders file
                with open('order_status.json', 'w') as file:
                    json.dump([], file, indent=2)
                logger.info("Created new order status file")
            except json.JSONDecodeError:
                logger.error("Invalid order status file format")
                # Don't overwrite potentially corrupted file
                return False
            return True
        except Exception as e:
            logger.error(f"Error initializing order status file: {str(e)}")
            return False
#"asst_lnCNiJMSzImHpVy3dsLYa9Xq"
#
    def create_assistant(self):
        """Connect to existing Azure OpenAI Assistant"""
        try:
            assistant_id = "asst_5cKN8dyUmyUKcRGAD7PmPJ0V"
            assistant = self.client.beta.assistants.retrieve(assistant_id)
            logger.info("Successfully connected to existing assistant")
            return assistant
        except Exception as e:
            logger.error(f"Error connecting to assistant: {str(e)}")
            raise

    def ask_question(self, question):
        """Process questions with thread and run handling"""
        try:
            # Start new thread if needed
            if not self.conversation_thread:
                self.start_conversation()
                self._last_topic = None
            
            # Get current topic
            current_topic = self._get_question_topic(question)
            logger.info(f"Current topic: {current_topic}")
            
            # Sync vector store for status queries
            if current_topic in ['order', 'complaint'] and 'status' in question.lower():
                logger.info("Syncing data before status query")
                self.initialize_vector_store()
            
            # Only create new thread if topic completely changes
            if self._last_topic and current_topic and self._last_topic != current_topic:
                logger.info(f"Topic changed from {self._last_topic} to {current_topic}")
                self.end_conversation()
                self.start_conversation()
            
            # Update last topic if we have a clear topic
            if current_topic:
                self._last_topic = current_topic
            
            logger.info(f"Processing question: {question}")

            # Handle delivery updates
            update_keywords = ['change delivery','change the delivery', 'update delivery', 'change date', 'update date', 'reschedule']
            is_update_request = any(keyword in question.lower() for keyword in update_keywords)
            
            if (current_topic == 'order' and is_update_request) or self.conversation_state['pending_action'] == 'delivery_update':
                # Extract order ID from current question
                order_match = re.search(r'order.*?(\d{6})', question.lower())
                
                # Multiple date patterns for flexibility
                date_patterns = [
                    r'(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z]+)\s+(\d{4})',  # 30th December 2024
                    r'([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})',          # December 30th 2024
                    r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})'                             # 30/12/2024
                ]
                
                date_match = None
                formatted_date = None
                
                # Try each date pattern
                for pattern in date_patterns:
                    match = re.search(pattern, question.lower())
                    if match:
                        try:
                            if match.group(1).isalpha():
                                # Month Day Year format
                                month = match.group(1)
                                day = match.group(2)
                                year = match.group(3)
                            else:
                                # Day Month Year format
                                day = match.group(1)
                                month = match.group(2)
                                year = match.group(3)
                            
                            # Format date consistently
                            formatted_date = f"{int(day)}th {month.capitalize()} {year}"
                            date_match = match
                            break
                        except Exception as e:
                            logger.error(f"Date parsing error: {str(e)}")
                            continue

                # If we have a pending order ID, use it
                if self.conversation_state['pending_order_id']:
                    order_id = self.conversation_state['pending_order_id']
                elif order_match:
                    order_id = order_match.group(1)
                    self.conversation_state['pending_order_id'] = order_id
                else:
                    order_id = None

                # Handle different states of the conversation
                if order_id and formatted_date:
                    # We have both order ID and date, process the update
                    self._reset_conversation_state()
                    response = self.handle_delivery_date_update(order_id, formatted_date)
                    if "updated" in response.lower():
                        # If update was successful, sync vector store
                        logger.info("Update successful, syncing vector store")
                        self.initialize_vector_store()
                    return response
                    
                elif order_id and not formatted_date:
                    # We have order ID but need date
                    self.conversation_state['pending_action'] = 'delivery_update'
                    self.conversation_state['pending_order_id'] = order_id
                    return "Could you please provide the new delivery date in format: 24th December 2024? This helps me ensure I get it exactly right."
                    
                elif not order_id:
                    # We need order ID
                    self.conversation_state['pending_action'] = 'delivery_update'
                    return "I'll help you reschedule your delivery. Could you please provide your order number and the new delivery date? For example: 'Change delivery date for order 123456 to 24th December 2024'"

            # Handle complaints
            elif current_topic == 'complaint' or self.conversation_state['pending_action'] == 'lodge_complaint':
                # Extract order ID and complaint text
                order_id_match = re.search(r'order.*?(\d{6})', question.lower())
                
                if self.conversation_state['pending_order_id']:
                    order_id = self.conversation_state['pending_order_id']
                elif order_id_match:
                    order_id = order_id_match.group(1)
                    self.conversation_state['pending_order_id'] = order_id
                else:
                    order_id = None

                if order_id:
                    complaint_text = re.sub(r'complaint|lodge|raise|register|about|order.*?\d{6}',
                                        '', question.lower()).strip()
                    self._reset_conversation_state()
                    response = self.lodge_complaint(
                        order_id=order_id,
                        complaint_text=complaint_text
                    )
                    if "registered" in response.lower():
                        # If complaint was lodged successfully, sync vector store
                        logger.info("Complaint lodged, syncing vector store")
                        self.initialize_vector_store()
                    return response
                else:
                    self.conversation_state['pending_action'] = 'lodge_complaint'
                    return "I'll help you file a complaint. Could you please provide your order number? This will help me track your case better."
                
            try:
                # Format the question with topic context
                formatted_question = f"{current_topic.upper()}: {question}" if current_topic else question

                # Add message to thread
                message = self.client.beta.threads.messages.create(
                    thread_id=self.conversation_thread.id,
                    role="user",
                    content=formatted_question
                )
                
                # Build comprehensive instructions
                instructions = """You are a JLP customer service assistant with access to all customer data. Use these guidelines:
        1. Never say you don't have access to data
        2. Never mention the source where you are providing information
        3. Don't identify as an AI
        4. Always verify IDs before sharing information
        5. Keep responses natural and helpful
        6. If information isn't found, say "I don't see that in our records"
        7. Format dates as: 28th November 2024
        8. Never mention contacting customer service
        9. Keep responses conversational and human-like
        10. Remove any source citations from responses"""

                # Add topic-specific instructions
                if current_topic == 'order':
                    instructions += """
                    IMPORTANT ORDER INSTRUCTIONS:
                    - Verify the order ID is 6 digits
                    - Check latest order details and status
                    - Always provide status and processing date
                    - Use format: "Your order #[ID] is [status]. You can expect your delivery on [date]."
                    - Only orders with status "Yet to be shipped" can have delivery dates updated
                    - If order not found: "I couldn't find that order number in our records"
                    - Read from the most recently uploaded file for latest updates
                    - Keep responses natural and conversational"""
                elif current_topic == 'complaint':
                    instructions += """
                    IMPORTANT COMPLAINT INSTRUCTIONS:
                    - Handle complaints through the local file system
                    - Get complaint ID format: COMPXXXXXXXXX where X is timestamp
                    - Always verify order ID before complaint handling
                    - Provide clear status updates and tracking information
                    - Keep customer informed of next steps
                    - Read from the most recently uploaded file for latest updates
                    - Keep responses empathetic and understanding"""

                # Create and monitor run
                run = self._create_and_monitor_run(
                    thread_id=self.conversation_thread.id,
                    assistant_id=self.assistant.id,
                    content=formatted_question,
                    instructions=instructions
                )
                
                if run and run.status == 'completed':
                    response = self._get_run_response(run)
                    if response:
                        return response

                return self._get_fallback_response(current_topic)
                    
            except Exception as e:
                logger.error(f"Error in assistant interaction: {str(e)}")
                raise

        except Exception as e:
            logger.error(f"Error in ask_question: {str(e)}")
            return "I apologize, but I'm having trouble with that request. Could you please try again?"

    def lodge_complaint(self, order_id, complaint_text, customer_name=None, mobile_no=None):
        """Lodge a new complaint in local file"""
        try:
            # Create new complaint
            timestamp = int(time.time())
            complaint_id = f"COMP{timestamp}"
            
            new_complaint = {
                "Complaint ID": complaint_id,
                "Order ID": str(order_id),
                "Customer Name": customer_name,
                "Mobile No": mobile_no,
                "Complaint Text": complaint_text,
                "Status": "Open",
                "Date Lodged": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "Last Updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "Priority": "Medium",
                "Category": "General",
                "Resolution Notes": None
            }

            try:
                # Read existing complaints from local file
                try:
                    with open('complaints.json', 'r') as file:
                        complaints_data = json.load(file)
                except (FileNotFoundError, json.JSONDecodeError) as e:
                    logger.warning(f"Could not read complaints.json: {str(e)}")
                    complaints_data = []

                # Ensure complaints_data is a list
                if not isinstance(complaints_data, list):
                    complaints_data = []
                
                # Add new complaint
                complaints_data.append(new_complaint)

                # Write updated complaints back to file
                try:
                    with open('complaints.json', 'w') as file:
                        json.dump(complaints_data, file, indent=2)
                    
                    logger.info(f"Successfully lodged complaint {complaint_id} for order {order_id}")
                    return f"Your complaint has been registered with ID: {complaint_id}. We will look into this matter and get back to you soon."
                    
                except Exception as write_error:
                    logger.error(f"Failed to write complaints file: {str(write_error)}")
                    return f"I've recorded your complaint but encountered a technical issue. Please note down this reference number for tracking: {complaint_id}"
                
            except Exception as e:
                logger.error(f"Error processing complaint: {str(e)}")
                return f"I've recorded your complaint but encountered a technical issue. Please note down this reference number for tracking: {complaint_id}"
                
        except Exception as e:
            logger.error(f"Critical error in complaint handling: {str(e)}")
            return "We're experiencing technical difficulties. Please try again later."
        
    def get_complaint_status(self, complaint_id=None, order_id=None):
        """Get complaint status from local file"""
        try:
            # Read complaints from local file
            try:
                with open('complaints.json', 'r') as file:
                    complaints_data = json.load(file)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.error(f"Could not read complaints.json: {str(e)}")
                return "Unable to retrieve complaint information at this time."

            if not isinstance(complaints_data, list):
                logger.error("Invalid complaints data format")
                return "Unable to retrieve complaint information at this time."
            
            # Find matching complaint
            complaint = None
            if complaint_id:
                complaint = next((c for c in complaints_data 
                                if c.get("Complaint ID") == complaint_id), None)
            elif order_id:
                complaint = next((c for c in complaints_data 
                                if c.get("Order ID") == str(order_id)), None)
            
            if complaint:
                last_updated = datetime.strptime(
                    complaint['Last Updated'], 
                    "%Y-%m-%dT%H:%M:%S"
                ).strftime("%d %B %Y")
                
                status_msg = f"""Complaint Status Details:
                Complaint ID: {complaint['Complaint ID']}
                Status: {complaint['Status']}
                Category: {complaint.get('Category', 'General')}
                Priority: {complaint.get('Priority', 'Medium')}
                Last Updated: {last_updated}
                Order ID: {complaint['Order ID']}
                Complaint: {complaint['Complaint Text']}"""
                
                if complaint.get('Resolution Notes'):
                    status_msg += f"\nResolution Notes: {complaint['Resolution Notes']}"
                
                return status_msg
            else:
                return "No complaint found with the provided details."

        except Exception as e:
            logger.error(f"Error in complaint status check: {str(e)}")
            return "Unable to retrieve complaint status at this time."
        
    def initialize_complaints_file(self):
        """Initialize complaints.json if it doesn't exist"""
        try:
            try:
                with open('complaints.json', 'r') as file:
                    json.load(file)  # Validate JSON format
                    logger.info("Complaints file exists and is valid")
            except FileNotFoundError:
                # Create initial empty complaints file
                with open('complaints.json', 'w') as file:
                    json.dump([], file, indent=2)
                logger.info("Created new complaints file")
            except json.JSONDecodeError:
                # File exists but is invalid JSON, reset it
                with open('complaints.json', 'w') as file:
                    json.dump([], file, indent=2)
                logger.info("Reset complaints file due to invalid format")
        except Exception as e:
            logger.error(f"Error initializing complaints file: {str(e)}")
    
    def initialize_vector_store(self):
        """Initialize vector store with order_status.json and complaints.json"""
        try:
            # Read files
            try:
                with open('order_status.json', 'r') as file:
                    orders_data = json.load(file)
                
                with open('complaints.json', 'r') as file:
                    complaints_data = json.load(file)
            except Exception as e:
                logger.error(f"Error reading files: {str(e)}")
                return False

            try:
                vector_store_id = self.assistant.tool_resources.file_search.vector_store_ids[0]
                
                # Update vector store content
                order_content = {
                    "text": json.dumps(orders_data, indent=4),
                    "metadata": {
                        "type": "order_status",
                        "last_updated": datetime.now().isoformat()
                    }
                }

                complaint_content = {
                    "text": json.dumps(complaints_data, indent=4),
                    "metadata": {
                        "type": "complaints",
                        "last_updated": datetime.now().isoformat()
                    }
                }

                # Update vector store using beta API
                self.client.beta.vector_stores.update(
                    vector_store_id=vector_store_id,
                    content=order_content
                )

                self.client.beta.vector_stores.update(
                    vector_store_id=vector_store_id,
                    content=complaint_content
                )
                
                logger.info("Successfully updated vector store")
                return True
                
            except Exception as e:
                logger.error(f"Error uploading files to vector store: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"Error in vector store initialization: {str(e)}")
            return False
        
    def update_complaint_status(self, complaint_id, new_status, resolution_notes=None):
        """Update complaint status in Azure vector store"""
        try:
            vector_store_id = self.assistant.tool_resources.file_search.vector_store_ids[0]
            
            try:
                # First get the existing complaint
                response = self.client.beta.vector_stores.query(
                    vector_store_id=vector_store_id,
                    query=f"id:{complaint_id}",
                    metadata_filter={"type": "complaint"},
                    top_k=1
                )

                if response.matches:
                    complaint = json.loads(response.matches[0].content)
                    
                    # Update complaint details
                    complaint['Status'] = new_status
                    complaint['Last Updated'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    if resolution_notes:
                        complaint['Resolution Notes'] = resolution_notes

                    # Update in vector store
                    self.client.beta.vector_stores.update(
                        vector_store_id=vector_store_id,
                        vectors=[{
                            "id": complaint_id,
                            "content": json.dumps(complaint),
                            "metadata": {
                                "type": "complaint",
                                "order_id": complaint['Order ID'],
                                "status": new_status,
                                "updated_at": datetime.now().isoformat()
                            }
                        }]
                    )
                    return f"Complaint {complaint_id} status updated to {new_status}"
                else:
                    return f"Complaint {complaint_id} not found"

            except Exception as e:
                logger.error(f"Error updating complaint: {str(e)}")
                return "Unable to update complaint status at this time."
                
        except Exception as e:
            logger.error(f"Critical error in complaint update: {str(e)}")
            return "We're experiencing technical difficulties. Please try again later."
        
    def handle_delivery_date_update(self, order_id, new_date):
        """Update delivery date in order_status.json"""
        logger.info(f"Starting delivery date update for order {order_id} to {new_date}")
        
        # First read and validate the order exists
        try:
            with open('order_status.json', 'r') as file:
                orders_data = json.load(file)
                logger.info("Successfully read order_status.json")
        except Exception as e:
            logger.error(f"Failed to read order_status.json: {e}")
            return "I'm having trouble accessing the order information right now."

        # Find the order and validate
        order_to_update = None
        for idx, order in enumerate(orders_data):
            if str(order.get("Order ID")) == str(order_id):
                order_to_update = order
                order_index = idx
                break
        
        if not order_to_update:
            return f"I couldn't find order #{order_id} in our records."

        # Check order status
        if order_to_update["Order Status"].lower() != "yet to be shipped":
            return f"I'm sorry, but order #{order_id} cannot be updated as it has already been {order_to_update['Order Status']}."

        try:
            # Convert and validate dates
            current_date = datetime.strptime(order_to_update["Date"], "%Y-%m-%dT00:00:00")
            new_date_str = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', new_date)
            new_date_obj = datetime.strptime(new_date_str.strip(), "%d %B %Y")
            
            # Check if new date is earlier
            if new_date_obj <= current_date:
                return f"I can only reschedule to a date after the current delivery date ({current_date.strftime('%d %B %Y')})"

            # Update the order
            orders_data[order_index]["Date"] = new_date_obj.strftime("%Y-%m-%dT00:00:00")
            
            # Write back to file
            with open('order_status.json', 'w') as file:
                json.dump(orders_data, file, indent=4)
                logger.info(f"Successfully updated order_status.json with new date {new_date_obj.strftime('%Y-%m-%dT00:00:00')}")

            # Verify the update
            with open('order_status.json', 'r') as file:
                verify_data = json.load(file)
                verify_order = next((o for o in verify_data if str(o["Order ID"]) == str(order_id)), None)
                if verify_order["Date"] != new_date_obj.strftime("%Y-%m-%dT00:00:00"):
                    raise Exception("File verification failed - date not updated")
                
            # Update vector store
            try:
                vector_store_id = self.assistant.tool_resources.file_search.vector_store_ids[0]
                vector_content = {
                    "data": orders_data,
                    "metadata": {
                        "type": "order_status",
                        "last_updated": datetime.now().isoformat()
                    }
                }
                self.client.beta.vector_stores.update(
                    vector_store_id=vector_store_id,
                    data=vector_content
                )
                logger.info("Successfully updated vector store")
            except Exception as ve:
                logger.error(f"Failed to update vector store: {ve}")
                # Continue since file was updated

            return f"Perfect! I've updated your delivery date for order #{order_id}. You can expect your delivery on {new_date_obj.strftime('%d %B %Y')}."

        except Exception as e:
            logger.error(f"Failed to update delivery date: {e}")
            return "I'm having trouble updating the delivery date. Could you please try again?"
        
    def get_existing_files(self):
        """Get IDs of all files from vector store"""
        try:
            # Retrieve the assistant
            assistant = self.client.beta.assistants.retrieve(self.assistant.id)
            
            # Access vector store IDs from tool_resources
            vector_store_ids = assistant.tool_resources.file_search.vector_store_ids
            logger.info(f"Vector store IDs: {vector_store_ids}")
            
            # Get file details from vector store
            file_details = []
            if vector_store_ids:
                for store_id in vector_store_ids:
                    logger.info(f"Accessing vector store: {store_id}")
                    
                    # Get files from vector store
                    store_files = self.client.beta.vector_stores.files.list(store_id)
                    
                    for file in store_files.data:
                        # Extract available information directly
                        file_details.append({
                            'id': file.id,
                            'created_at': getattr(file, 'created_at', None),
                            'status': getattr(file, 'status', None),
                            'vector_store_id': getattr(file, 'vector_store_id', store_id),
                            'usage_bytes': getattr(file, 'usage_bytes', None)
                        })
                        logger.info(f"File object details: {file.__dict__}")
                        logger.info(f"Found file with ID: {file.id},  Filename: {getattr(file, 'filename', 'Unknown Filename')}")
            
            return file_details
            
        except Exception as e:
            logger.error(f"Error retrieving file IDs: {str(e)}")
            return None

    def get_file_by_prefix(self, prefix):
        """Get file ID by matching prefix"""
        try:
            files = self.get_existing_files()
            if files:
                for file in files:
                    # Match the prefix in the file ID
                    if str(file['id']).lower().startswith(prefix.lower()):
                        logger.info(f"Found matching file: {file['id']}")
                        return file['id']
            
            logger.warning(f"No matching file found for prefix: {prefix}")
            return None
            
        except Exception as e:
            logger.error(f"Error finding file by prefix: {str(e)}")
            return None

    # Usage example
    def find_order_status_file(self):
        """Find Order Status file ID"""
        try:
            # Look for file with appropriate prefix or pattern
            file_id = self.get_file_by_prefix('assistant-')
            if file_id:
                logger.info(f"Found Order Status file: {file_id}")
            else:
                logger.warning("Order Status file not found")
            return file_id
        except Exception as e:
            logger.error(f"Error finding Order Status file: {str(e)}")
            return None

    def handle_active_run(self, thread_id):
        """Handle any active runs on the thread"""
        try:
            runs = self.client.beta.threads.runs.list(
                thread_id=thread_id,
                order="desc",
                limit=1
            )
            
            for run in runs:
                if run.status in ['queued', 'in_progress', 'requires_action']:
                    try:
                        # Try to cancel the active run
                        self.client.beta.threads.runs.cancel(
                            thread_id=thread_id,
                            run_id=run.id
                        )
                        logger.info(f"Cancelled active run: {run.id}")
                        time.sleep(1)  # Wait for cancellation to complete
                        return True
                    except Exception as e:
                        logger.error(f"Error cancelling run: {str(e)}")
                        # If can't cancel, create new thread
                        self.end_conversation()
                        self.start_conversation()
                        return True
        except Exception as e:
            logger.error(f"Error checking active runs: {str(e)}")
            return False
        return False

    def _create_and_monitor_run(self, thread_id, assistant_id, content, instructions):
        """Create and monitor run with improved error handling"""
        try:
            # Create run with retry logic
            max_retries = 2
            retry_count = 0
            
            # Create initial run
            run = self.client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=assistant_id,
                instructions=instructions,
                temperature=0.1
            )
            
            # Monitor run with timeout
            start_time = time.time()
            while True:
                if time.time() - start_time > 10:  # 10 second timeout
                    if retry_count < max_retries:
                        # Retry with same thread
                        logger.info("Run timed out, retrying...")
                        
                        run = self.client.beta.threads.runs.create(
                            thread_id=thread_id,
                            assistant_id=assistant_id,
                            instructions=instructions,
                            temperature=0.1
                        )
                        start_time = time.time()
                        retry_count += 1
                        continue
                    else:
                        return None
                    
                try:
                    time.sleep(0.5)
                    run = self.client.beta.threads.runs.retrieve(
                        thread_id=thread_id,
                        run_id=run.id
                    )
                    
                    if run.status == 'completed':
                        return run
                    elif run.status in ['failed', 'cancelled', 'expired']:
                        if retry_count < max_retries:
                            # Retry on failure
                            run = self.client.beta.threads.runs.create(
                                thread_id=thread_id,
                                assistant_id=assistant_id,
                                instructions=instructions,
                                temperature=0.1
                            )
                            start_time = time.time()
                            retry_count += 1
                            continue
                        else:
                            return None
                            
                except Exception as e:
                    logger.error(f"Error in run monitoring: {str(e)}")
                    if retry_count < max_retries:
                        retry_count += 1
                        continue
                    return None
                    
        except Exception as e:
            logger.error(f"Error creating run: {str(e)}")
            return None

    def _get_run_response(self, run):
        """Get response from completed run with response cleaning"""
        try:
            messages = self.client.beta.threads.messages.list(
                thread_id=self.conversation_thread.id,
                order="desc",
                limit=1
            )
            
            for message in messages:
                if message.role == "assistant":
                    response = message.content[0].text.value
                    # Clean the response before returning
                    return self._clean_response(response)
                    
            return None
            
        except Exception as e:
            logger.error(f"Error getting run response: {str(e)}")
            return None

    def start_conversation(self):
        """Initialize a new conversation thread with error handling"""
        try:
            self.conversation_thread = self.client.beta.threads.create()
            logger.info(f"Created new conversation thread: {self.conversation_thread.id}")
            return True
        except Exception as e:
            logger.error(f"Error creating conversation thread: {str(e)}")
            # Wait briefly and retry once
            time.sleep(1)
            try:
                self.conversation_thread = self.client.beta.threads.create()
                logger.info(f"Created new conversation thread on retry: {self.conversation_thread.id}")
                return True
            except Exception as retry_e:
                logger.error(f"Error creating conversation thread on retry: {str(retry_e)}")
                return False
    def _reset_conversation_state(self):
        """Reset conversation state"""
        self.conversation_state = {
            'pending_order_id': None,
            'pending_action': None,
            'last_topic': None
        }

    def end_conversation(self):
        """Clean up conversation thread"""
        try:
            if self.conversation_thread:
                # No need to explicitly delete the thread, just clear our reference
                logger.info(f"Ending conversation thread: {self.conversation_thread.id}")
                self.conversation_thread = None
        except Exception as e:
            logger.error(f"Error ending conversation thread: {str(e)}")

    def _get_topic_instructions(self, topic):
        """Get detailed instructions based on topic"""
        base_instructions = """You are a JLP customer service assistant. Guidelines:
        1. Never say you don't have access to data
        2. Give specific, detailed responses
        3. For locations, provide full address and timings
        4. For products, include prices and availability
        5. Format dates as: DD Month YYYY"""

        topic_specific = {
            'store': """For Store Queries:
        - Check JLP_store.json for store details
        - Provide exact address, opening hours, and contact details
        - Include available services at each location
        - If store not found, suggest nearest alternative""",
            
            'product': """For Product Queries:
        - Check product.json for specifications
        - Include price, availability, and color options
        - Provide delivery estimates
        - Suggest alternatives if not available""",
            
            'return': """For Return Queries:
        - Check Returns.json for status
        - Provide return processing time
        - Include refund status if applicable
        - Explain next steps""",
            
            'order': """For Order Status:
        - Check Order_Status.json for latest status
        - Include delivery date and tracking info
        - Mention any updates or changes
        - Use format: Your order #[ID] is [status]"""
        }

        return base_instructions + "\n" + topic_specific.get(topic, "")

    def handle_conversation(self, stop_event):
        """Handle conversation with scottish support and maintain context"""
        try:
            if stop_event.is_set():
                self.end_conversation()
                return False

            # Select language if not already done
            if not self.language_selected:
                language = self.select_language()
                is_scottish = language == "scottish"
                
                # Start new conversation thread
                self.start_conversation()
                # Welcome message in appropriate language
                welcome_msg = self.get_welcome_message(is_scottish)
                self.speak(welcome_msg)
                return (True, language)
            
            while not stop_event.is_set():
                try:
                    question = self.listen().lower()
                    
                    if stop_event.is_set():
                        self.end_conversation()
                        return False
                    
                    if not question:
                        response = "I didnae catch that. Could ye repeat?" if self.current_language == "en-GB" else "I couldn't hear you. Could you please repeat?"
                        self.speak(response)
                        continue

                    # Handle exit commands
                    exit_commands = ["bye", "exit", "cheerio", "farewell", "goodbye"]
                    if any(cmd in question.lower() for cmd in exit_commands):
                        goodbye = "Thank ye for choosing JLP. Have a grand day!" if self.current_language == "en-GB" else "Thank you for choosing JLP. Have a great day!"
                        self.speak(goodbye)
                        self.end_conversation()
                        return False
                    
                    # Track current topic and add context for follow-up questions
                    try:
                        previous_messages = self.client.beta.threads.messages.list(
                            thread_id=self.conversation_thread.id,
                            order="desc",
                            limit=3
                        ) if self.conversation_thread else []
                        
                        msg_text = question.lower()
                        
                        # Define keyword lists for English
                        product_keywords = [
                            "chair", "table", "bed", "sofa", "desk", "shelf", "cabinet", "wardrobe",
                            "dining", "living", "bedroom", "kitchen", "bathroom", "furniture",
                            "available", "stock", "color", "size", "price", "cost", "material",
                            "dimensions", "measurement", "look", "style", "design"
                        ]
                        
                        order_keywords = [
                            "order", "delivery", "track", "status", "shipped", "package", 
                            "arriving", "received", "delayed", "id", "number", "#"
                        ]
                        
                        store_keywords = [
                            "store", "location", "address", "open", "close", "hours", "timing",
                            "nearest", "visit", "direction", "parking", "shop"
                        ]
                        
                        return_keywords = [
                            "return", "refund", "cancel", "exchange", "broken", "damaged",
                            "replacement", "money back", "policy"
                        ]
                        
                        # Check current message first
                        current_topic = None
                        msg_text_lower = msg_text.lower()
                        
                        # Check for topics in both languages
                        if any(keyword in msg_text_lower for keyword in order_keywords):
                            current_topic = "order"
                        elif any(keyword in msg_text_lower for keyword in product_keywords):
                            current_topic = "product"
                        elif any(keyword in msg_text_lower for keyword in store_keywords):
                            current_topic = "store"
                        elif any(keyword in msg_text_lower for keyword in return_keywords):
                            current_topic = "return"
                        
                        # If no topic detected in current message, check previous messages
                        if not current_topic:
                            for msg in previous_messages:
                                if msg.role == "user":
                                    msg_text = msg.content[0].text.value.lower()
                                    if any(keyword in msg_text for keyword in order_keywords):
                                        current_topic = "order"
                                    elif any(keyword in msg_text for keyword in product_keywords):
                                        current_topic = "product"
                                    elif any(keyword in msg_text for keyword in store_keywords):
                                        current_topic = "store"
                                    elif any(keyword in msg_text for keyword in return_keywords):
                                        current_topic = "return"
                                    if current_topic:
                                        break
                            
                            # Add context for follow-up questions
                            if current_topic:
                                if self.current_language == "en-GB":
                                    context_prefix = {
                                        "order": "About yer previous order: ",
                                        "product": "About yer previous product: ",
                                        "store": "About yer previous store inquiry: ",
                                        "return": "About yer previous return: "
                                    }.get(current_topic, "")
                                    question = f"{context_prefix} {question}"
                                else:
                                    question = f"Regarding the previous {current_topic} inquiry: {question}"
                                
                    except Exception as e:
                        logger.error(f"Error processing conversation context: {str(e)}")
                        # Continue without context if there's an error
                        pass
                    
                    # Process the question
                    try:
                        # Handle scottish translation
                        if self.current_language == "en-GB":
                            try:
                                # Translate question from scottish to English
                                english_question = self.translate_text(question, to_scottish=False)
                                logger.info(f"Translated question from Scottish: {english_question}")
                                
                                # Get answer in English
                                english_answer = self.ask_question(english_question)
                                
                                if stop_event.is_set():
                                    self.end_conversation()
                                    return False
                                
                                # Translate answer to scottish
                                scottish_answer = self.translate_text(english_answer, to_scottish=True)
                                logger.info(f"Translated answer to Scottish: {scottish_answer}")
                                self.speak(scottish_answer)
                                
                            except Exception as translation_error:
                                logger.error(f"Translation error: {str(translation_error)}")
                                # Fallback to English if translation fails
                                answer = self.ask_question(question)
                                self.speak(answer)
                        else:
                            # Process in English directly
                            answer = self.ask_question(question)
                            self.speak(answer)
                            
                    except Exception as e:
                        logger.error(f"Error processing question: {str(e)}")
                        error_msg = "I'm terribly sorry, could you try that again?" if self.current_language == "en-GB" else "I encountered an error. Please try again."
                        self.speak(error_msg)   
                        
                except Exception as e:
                    logger.error(f"Error in conversation loop: {str(e)}")
                    continue
            
            self.end_conversation()
            return False
            
        except Exception as e:
            logger.error(f"Error in conversation handler: {str(e)}")
            self.end_conversation()
            return False

    def _get_question_topic(self, question):
        """Enhanced topic detection with better pattern matching"""
        try:
            question_lower = question.lower()
            
            # Order-related patterns
            order_patterns = [
                r'order.*?(?:id|number|#)?\s*(?:is|:)?\s*(\d{6})',
                r'order.*?status.*?(\d{6})',
                r'(\d{6}).*?order',
                r'status.*?order.*?(\d{6})',
                r'order.*?(\d{6})'
            ]

            complaint_patterns = [
                r'complaint.*?(?:id|number|#)?\s*(?:is|:)?\s*(\w+)',
                r'raise.*?complaint',
                r'lodge.*?complaint',
                r'register.*?complaint',
                r'complain.*?about'
            ]
            
            # Topic keywords with weights
            topic_keywords = {
                'order': {
                    'high': ['order', 'delivery', 'tracking'],
                    'medium': ['status', 'shipped', 'package'],
                    'low': ['received', 'arriving']
                },
                'complaint': {
                    'high': ['complaint', 'grievance', 'issue', 'problem'],
                    'medium': ['raise', 'lodge', 'register', 'report'],
                    'low': ['unhappy', 'dissatisfied', 'wrong']
                },
                'product': {
                    'high': ['chair', 'table', 'sofa', 'specifications'],
                    'medium': ['product', 'furniture', 'details'],
                    'low': ['available', 'color', 'price']
                },
                'store': {
                    'high': ['store', 'location', 'address'],
                    'medium': ['hours', 'directions', 'parking'],
                    'low': ['open', 'visit', 'nearest']
                },
                'return': {
                    'high': ['return', 'refund', 'exchange'],
                    'medium': ['policy', 'cancel', 'replacement'],
                    'low': ['broken', 'damaged', 'money back']
                }
            }
            
            # Check for order numbers first
            for pattern in complaint_patterns:
                if re.search(pattern, question_lower):
                    return 'complaint'
            for pattern in order_patterns:
                if re.search(pattern, question_lower):
                    return 'order'
            
            # Calculate topic scores
            topic_scores = {topic: 0 for topic in topic_keywords}
            
            for topic, keywords in topic_keywords.items():
                # High priority keywords (3 points)
                topic_scores[topic] += sum(3 for word in keywords['high'] if word in question_lower)
                # Medium priority keywords (2 points)
                topic_scores[topic] += sum(2 for word in keywords['medium'] if word in question_lower)
                # Low priority keywords (1 point)
                topic_scores[topic] += sum(1 for word in keywords['low'] if word in question_lower)
            
            # Get highest scoring topic
            max_score = max(topic_scores.values())
            if max_score > 0:
                return max(topic_scores.items(), key=lambda x: x[1])[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Error in topic detection: {str(e)}")
            return None

    def _get_fallback_response(self, topic):
        """Get improved fallback responses based on topic and context"""
        try:
            # Check for order/return IDs in the question
            if hasattr(self, 'last_question'):
                id_match = re.search(r'\d{6,8}', self.last_question)
                if id_match:
                    invalid_id = id_match.group()
                    
                    if topic == 'order':
                        return f"I apologize, but I couldn't find order #{invalid_id} in our system. Please double-check the order number and try again."
                    elif topic == 'return':
                        return f"I apologize, but I couldn't find return #{invalid_id} in our system. Please verify the return number and try again."

            # Standard fallback responses
            if topic == 'product':
                if hasattr(self, 'last_question') and len(self.last_question) > 5:
                    # Extract potential product terms
                    words = self.last_question.lower().split()
                    product_terms = [w for w in words if len(w) > 3]  # Filter meaningful words
                    if product_terms:
                        return f"I apologize, but I couldn't find enough information about that {product_terms[-1]}. Could you please provide more details or try a different product?"
                return "Could you please specify which product you're interested in? You can ask about any JLP furniture or home accessories."
                
            elif topic == 'store':
                if hasattr(self, 'last_question') and len(self.last_question) > 5:
                    return "I couldn't find that specific store location. Could you please try with a different city or area name?"
                return "Which JLP store location would you like information about?"
                
            elif topic == 'order':
                return "To check your order status, please provide your order number. It should be a 6-8 digit number found in your order confirmation email."
                
            elif topic == 'return':
                return "To check your return status, please provide your return reference number. You can find this in your return confirmation email."
                
            else:
                return "I'm here to help! You can ask me about:\n- Product information\n- Store locations and hours\n- Order status (with order number)\n- Return status (with return number)"
                
        except Exception as e:
            logger.error(f"Error in fallback response: {str(e)}")
            return "I apologize, but I'm having trouble understanding. Could you please rephrase your question?"

    def _handle_speech_error(self, error_type):
        """Handle speech recognition errors with better responses"""
        if error_type == speechsdk.CancellationReason.Error:
            return "I'm having trouble hearing you clearly. Could you please speak a bit louder?"
        elif error_type == speechsdk.CancellationReason.NoMatch:
            return "I didn't catch that. Could you please repeat?"
        else:
            return "I couldn't hear you. Could you please try again?"
        
    def _clean_response(self, response):
        """Clean response to remove citations and format properly"""
        try:
            if not response:
                return response
                
            # Remove all types of source citations
            import re
            source_patterns = [
                r'【\d+:\d+†source】',  # Matches 【4:8†source】
                r'【\d+:\d+】',         # Matches 【4:8】
                r'\[\d+:\d+\]',        # Matches [4:8]
                r'【\d+†source】',      # Matches 【4†source】
                r'【source】',          # Matches 【source】
                r'\[\d+\]',            # Matches [4]
                r'《\d+:\d+》',         # Matches 《4:8》
                r'「\d+:\d+」'          # Matches 「4:8」
            ]
            
            for pattern in source_patterns:
                response = re.sub(pattern, '', response)

            # Remove asterisks from text
            response = re.sub(r'\*\*', '', response)
            response = re.sub(r'\*', '', response)
            
            # Clean up extra spaces
            response = ' '.join(response.split())
            
            # Format dates consistently
            date_patterns = [
                (r'\d{4}-\d{2}-\d{2}', '%Y-%m-%d'),  # YYYY-MM-DD
                (r'\d{2}/\d{2}/\d{4}', '%d/%m/%Y'),  # DD/MM/YYYY
                (r'\d{1,2} [A-Za-z]+ \d{4}', '%d %B %Y')  # DD Month YYYY
            ]
            
            for pattern, format_str in date_patterns:
                dates = re.finditer(pattern, response)
                for date_match in dates:
                    try:
                        from datetime import datetime
                        date_str = date_match.group()
                        date_obj = datetime.strptime(date_str, format_str)
                        
                        # Format day with suffix
                        day = date_obj.day
                        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 20, 'th')
                        if 11 <= day <= 13:
                            suffix = 'th'
                            
                        formatted_date = f"{day}{suffix} {date_obj.strftime('%B %Y')}"
                        response = response.replace(date_str, formatted_date)
                    except:
                        continue
            
            # Ensure proper spacing after punctuation
            response = re.sub(r'([.,!?])([^\s])', r'\1 \2', response)
            
            # Remove multiple spaces
            response = ' '.join(response.split())
            
            # Clean up any remaining formatting artifacts
            response = re.sub(r'_+', '', response)  # Remove underscores
            response = re.sub(r'~+', '', response)  # Remove tildes
            response = re.sub(r'`+', '', response)  # Remove backticks
            
            return response.strip()
            
        except Exception as e:
            logger.error(f"Error cleaning response: {str(e)}")
            return response
        
    def emergency_stop(self):
        """Emergency stop with proper cleanup"""
        try:
            logger.info("Initiating emergency stop...")
            
            # End the conversation thread
            self.end_conversation()
            
            # Stop speech synthesis
            if hasattr(self, 'speech_synthesizer'):
                self.speech_synthesizer.stop_speaking_async()
            
            # Stop speech recognition
            if hasattr(self, 'speech_recognizer'):
                self.speech_recognizer.stop_continuous_recognition_async()
            
            # Clear audio configs
            self.audio_config = None
            self.audio_output_config = None
            
            logger.info("Emergency stop completed")
            
        except Exception as e:
            logger.error(f"Error during emergency stop: {str(e)}")


    # [Rest of the methods remain the same as in your original code]
    def translate_text(self, text, to_scottish=False):
        """Translate text between English and scottish with proper initialization"""
        try:
            if not text:
                return text
            
            if to_scottish:
                translator = GoogleTranslator(source='en', target='gd')
            else:
                translator = GoogleTranslator(source='gd', target='en')
            
            return translator.translate(text)
        except Exception as e:
            logger.error(f"Translation error: {str(e)}")
            return text  # Return original text if translation fails

    def get_welcome_message(self, is_scottish=False):
        """Get appropriate welcome message based on language"""
        if is_scottish:
            return "How can I help ye today?"
        return "How can I help you today?"
    
    def _init_speech_services(self):
        """Initialize speech services with proper configuration"""
        try:
            # Configure speech synthesizer
            self.speech_synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=self.speech_config,
                audio_config=self.audio_output_config
            )
            
            # Configure speech recognizer
            self.speech_recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=self.audio_config
            )
            
            # Set scottish-specific properties if needed
            if self.current_language == "en-GB":
                self.speech_config.speech_synthesis_voice_name = "en-GB-LibbyNeural"
                self.speech_config.speech_recognition_language = "en-GB"
                
            logger.info(f"Speech services initialized for language: {self.current_language}")
            
        except Exception as e:
            logger.error(f"Failed to initialize speech services: {str(e)}")
            raise

    def speak(self, text):
        """Enhanced speak function with better scottish support"""
        try:
            if not text:
                return
                
            # Update voice based on language
            if self.current_language == "en-GB":
                self.speech_config.speech_synthesis_voice_name = "en-GB-LibbyNeural"
            else:
                self.speech_config.speech_synthesis_voice_name = "en-US-AvaNeural"
            
            # Reinitialize synthesizer with updated config
            self.speech_synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=self.speech_config,
                audio_config=self.audio_output_config
            )
            
            # Speak the text
            result = self.speech_synthesizer.speak_text_async(text).get()
            
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                logger.info(f"Assistant said: {text}")
            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation_details = result.cancellation_details
                logger.error(f"Speech synthesis canceled: {cancellation_details.reason}")
                if cancellation_details.reason == speechsdk.CancellationReason.Error:
                    logger.error(f"Speech synthesis error: {cancellation_details.error_details}")
                    raise Exception(f"Speech synthesis error: {cancellation_details.error_details}")
            
        except Exception as e:
            logger.error(f"Error in speak function: {str(e)}")
            raise

    def listen(self):
        """Listen with improved error handling"""
        try:
            logger.info("Listening...")
            result = self.speech_recognizer.recognize_once_async().get()
            
            if result.reason == speechsdk.ResultReason.RecognizedSpeech:
                text = result.text.strip()
                logger.info(f"User said: {text}")
                return text
            elif result.reason == speechsdk.ResultReason.NoMatch:
                logger.warning(f"No speech could be recognized: {result.no_match_details}")
                return ""
            elif result.reason == speechsdk.ResultReason.Canceled:
                logger.error(f"Speech recognition canceled: {result.cancellation_details.reason}")
                return ""
                
        except Exception as e:
            logger.error(f"Error in listen function: {str(e)}")
            return ""


    def setup_english(self):
        """Configure for English"""
        try:
            self.current_language = "en-US"
            self.speech_config.speech_recognition_language = "en-US"
            self.speech_config.speech_synthesis_voice_name = 'en-US-AvaNeural'
            self.speak("English selected.")
        except Exception as e:
            logger.error(f"Error setting up English: {str(e)}")
            raise

    def setup_scottish(self):
        """Configure for Scottish English with proper speech settings"""
        try:
            # Set language to Scottish English
            self.current_language = "en-GB"  # Base language code for British English
            
            # Configure speech recognition for Scottish English
            self.speech_config.speech_recognition_language = "en-GB"  # Use British English for recognition
            
            # Configure Scottish voice
            self.speech_config.speech_synthesis_voice_name = "en-GB-LibbyNeural"  # Scottish voice option
            
            # Reinitialize speech services with new configuration
            self._init_speech_services()
            
            # Confirm language selection in Scottish English
            self.speak("Scottish English has been selected.")
            
            self.language_selected = True
            logger.info("Scottish language setup completed")
            
        except Exception as e:
            logger.error(f"Error setting up Scottish English: {str(e)}")
            raise


    def translate_response(self, text, to_scottish=False):
        """Translate text between English and scottish"""
        try:
            if not text:
                return text
            
            if to_scottish:
                return self.en_to_hi.translate(text)
            else:
                return self.hi_to_en.translate(text)
        except Exception as e:
            logger.error(f"Translation error: {str(e)}")
            return text

    def select_language(self):
        """Language selection with clear return values"""
        try:
            self.speak("Hi, Welcome to JLP. I am your Voice assistant, Please select your preferred language: Say 'English' or 'scottish'")
            
            max_attempts = 3
            for attempt in range(max_attempts):
                response = self.listen().lower()
                
                if "english" in response:
                    self.setup_english()
                    self.language_selected = True
                    return "english"
                elif "scottish" in response:
                    self.setup_scottish()
                    self.language_selected = True
                    return "scottish"
                else:
                    remaining = max_attempts - attempt - 1
                    if remaining > 0:
                        self.speak(f"Please say either 'English' or 'Scottish'. {remaining} attempts remaining.")
            
            # Default to English if no clear selection
            self.setup_english()
            self.language_selected = True
            return "english"
            
        except Exception as e:
            logger.error(f"Error in language selection: {str(e)}")
            self.setup_english()
            return "english"
            
