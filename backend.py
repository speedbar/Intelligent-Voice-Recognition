import os
import threading
import time
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from virtual_assistant import VirtualAssistant
from werkzeug.serving import WSGIRequestHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

WSGIRequestHandler.protocol_version = "HTTP/1.1"

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

class AssistantController:
    def __init__(self):
        self._running = False
        self._initializing = False
        self._lock = threading.RLock()
        self.thread = None
        self.instance = None
        self.stop_event = threading.Event()
        self.error_event = threading.Event()
        self._language_selected = False
        self._current_language = None

    @property
    def initializing(self):
        with self._lock:
            return self._initializing

    @initializing.setter
    def initializing(self, value):
        with self._lock:
            self._initializing = value

    @property
    def running(self):
        with self._lock:
            return self._running

    @running.setter
    def running(self, value):
        with self._lock:
            self._running = value

    @property
    def language_selected(self):
        with self._lock:
            return self._language_selected

    @language_selected.setter
    def language_selected(self, value):
        with self._lock:
            self._language_selected = value
            logger.info(f"Language selected state changed to: {value}")

    @property
    def current_language(self):
        with self._lock:
            return self._current_language

    @current_language.setter
    def current_language(self, value):
        with self._lock:
            if value:
                self._current_language = value
                self._language_selected = True
                logger.info(f"Current language set to: {value}")
            else:
                self._current_language = None
                self._language_selected = False
                logger.info("Language reset to None")

    def initialize_assistant(self):
        """Initialize assistant in a separate thread"""
        try:
            self.instance = VirtualAssistant()
            self.initializing = False
            self.running = True
            
            # Start the conversation thread
            self.thread = threading.Thread(
                target=self.run_assistant,
                args=(self.instance, self.stop_event, self.error_event),
                daemon=True
            )
            self.thread.start()
            
            logger.info("Assistant initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize assistant: {str(e)}")
            self.initializing = False
            self.running = False
            self.cleanup()
            raise

    def start(self):
        with self._lock:
            if self.running:
                return False
            if self.initializing:
                return True

            try:
                # Reset events and states
                self.stop_event.clear()
                self.error_event.clear()
                self.current_language = None  # This will also reset language_selected
                self.initializing = True
                
                # Start initialization in separate thread
                init_thread = threading.Thread(
                    target=self.initialize_assistant,
                    daemon=True
                )
                init_thread.start()
                
                logger.info("Assistant initialization started")
                return True

            except Exception as e:
                logger.error(f"Failed to start assistant: {str(e)}")
                self.initializing = False
                self.running = False
                self.cleanup()
                raise

    def stop(self):
        with self._lock:
            if not self.running:
                return True

            try:
                logger.info("Stopping assistant...")
                self.stop_event.set()
                self.running = False
                
                def force_stop():
                    time.sleep(3)
                    if self.thread and self.thread.is_alive():
                        logger.warning("Force stopping assistant")
                        self.cleanup()

                force_stop_thread = threading.Thread(target=force_stop, daemon=True)
                force_stop_thread.start()
                
                if self.thread:
                    self.thread.join(timeout=2)
                
                self.cleanup()
                logger.info("Assistant stopped successfully")
                return True

            except Exception as e:
                logger.error(f"Error stopping assistant: {str(e)}")
                self.cleanup()
                return False

    def cleanup(self):
        try:
            if self.instance:
                self.instance.emergency_stop()
            self.instance = None
            self.thread = None
            self._running = False
            self.current_language = None  # This will also reset language_selected
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

    def run_assistant(self, assistant, stop_event, error_event):
        try:
            while not stop_event.is_set() and not error_event.is_set():
                try:
                    result = assistant.handle_conversation(stop_event)
                    # Log the result for debugging
                    logger.info(f"handle_conversation result: {result}")
                    
                    if isinstance(result, tuple):
                        continue_flag, language = result
                        if language:
                            self.current_language = language
                            logger.info(f"Language updated from conversation: {language}")
                        if not continue_flag:
                            break
                    elif not result:
                        break
                    time.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error in conversation loop: {str(e)}")
                    error_event.set()
                    break
        except Exception as e:
            logger.error(f"Assistant thread critical error: {str(e)}")
            error_event.set()
        finally:
            logger.info("Assistant conversation ended")
            with self._lock:
                self._running = False

# Create controller instance
assistant_controller = AssistantController()

@app.route('/api/start-call', methods=['POST'])
def start_call():
    try:
        if assistant_controller.start():
            # Wait for a short time to see if initialization completes quickly
            time.sleep(2)
            if assistant_controller.running:
                return jsonify({
                    "status": "success",
                    "message": "Call started successfully"
                }), 200
            elif assistant_controller.initializing:
                return jsonify({
                    "status": "initializing",
                    "message": "Assistant is initializing"
                }), 202
            else:
                return jsonify({
                    "status": "error",
                    "message": "Initialization failed"
                }), 500
        else:
            return jsonify({
                "status": "error",
                "message": "Assistant already running"
            }), 400
    except Exception as e:
        logger.error(f"Error starting call: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/end-call', methods=['POST'])
def end_call():
    try:
        if assistant_controller.stop():
            return jsonify({
                "status": "success",
                "message": "Call ended successfully"
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Failed to stop assistant"
            }), 500
    except Exception as e:
        logger.error(f"Error ending call: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    try:
        # Log the current state for debugging
        logger.info(f"Current status - Running: {assistant_controller.running}, " 
                   f"Language Selected: {assistant_controller.language_selected}, "
                   f"Current Language: {assistant_controller.current_language}")
        
        return jsonify({
            "status": "success",
            "running": assistant_controller.running,
            "language_selected": assistant_controller.language_selected,
            "current_language": assistant_controller.current_language
        }), 200
    except Exception as e:
        logger.error(f"Error getting status: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/call-status', methods=['GET'])
def get_call_status():
    try:
        # Log the current state for debugging
        logger.info(f"Call status check - Running: {assistant_controller.running}, "
                   f"Language Selected: {assistant_controller.language_selected}, "
                   f"Current Language: {assistant_controller.current_language}")
        
        return jsonify({
            "status": "success",
            "running": assistant_controller.running,
            "language_selected": assistant_controller.language_selected,
            "language": assistant_controller.current_language
        }), 200
    except Exception as e:
        logger.error(f"Error getting call status: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == '__main__':
    try:
        from waitress import serve
        logging.getLogger('waitress').setLevel(logging.ERROR)
        
        print("Starting server on port 5000...")
        serve(
            app,
            host='127.0.0.1',
            port=5000,
            threads=8,
            channel_timeout=10,
            cleanup_interval=5,
            connection_limit=100,
            max_request_header_size=8192,
            max_request_body_size=1048576,
            url_scheme='http'
        )
    except ImportError:
        print("Waitress not installed, using Flask development server...")
        app.run(host='127.0.0.1', port=5000, threaded=True)