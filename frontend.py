import streamlit as st
import requests
from datetime import datetime
import time
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CallInterface:
    def __init__(self):
        self.backend_url = 'http://127.0.0.1:5000'
        self.initialize_session_state()
        self.setup_requests_session()

    def setup_requests_session(self):
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def initialize_session_state(self):
        if 'call_active' not in st.session_state:
            st.session_state.call_active = False
        if 'call_status' not in st.session_state:
            st.session_state.call_status = "Ready"
        if 'error_message' not in st.session_state:
            st.session_state.error_message = None
        if 'phone_number' not in st.session_state:
            st.session_state.phone_number = "1800-419-4532"
        if 'initializing' not in st.session_state:
            st.session_state.initializing = False
        if 'call_start_time' not in st.session_state:
            st.session_state.call_start_time = None

    def start_call(self):
        try:
            st.session_state.initializing = True
            with st.spinner("Starting JLP AI Assistant... This may take a minute..."):
                response = self.session.post(
                    f'{self.backend_url}/api/start-call',
                    json={
                        'timestamp': datetime.now().isoformat(),
                        'phone_number': st.session_state.phone_number
                    },
                    timeout=30
                )
                
                if response.status_code in [200, 202]:
                    st.session_state.call_active = True
                    st.session_state.call_status = "Call In Progress"
                    st.session_state.error_message = None
                    st.session_state.call_start_time = time.time()  # Record start time
                    st.success("JLP AI Assistant is ready!")
                elif response.status_code == 400:
                    st.session_state.call_active = True
                    st.session_state.call_status = "Call In Progress"
                    st.session_state.call_start_time = time.time()  # Record start time
                
        except requests.exceptions.Timeout:
            try:
                time.sleep(2)
                status_response = self.session.get(
                    f'{self.backend_url}/api/status',
                    timeout=5
                )
                if status_response.status_code == 200 and status_response.json().get('running', False):
                    st.session_state.call_active = True
                    st.session_state.call_status = "Call In Progress"
                    st.session_state.call_start_time = time.time()  # Record start time
                    st.success("JLP AI Assistant is ready!")
            except Exception:
                logger.error("Error during status check", exc_info=True)
        except Exception as e:
            logger.error("Error starting call", exc_info=True)
        finally:
            st.session_state.initializing = False

    def end_call(self):
        with st.spinner("Ending JLP AI Assistant..."):
            try:
                response = self.session.post(
                    f'{self.backend_url}/api/end-call',
                    json={'timestamp': datetime.now().isoformat()},
                    timeout=15
                )
                
                if response.status_code == 200:
                    st.success("Call ended successfully!")
                    st.session_state.call_active = False
                    st.session_state.call_status = "Ready"
                    st.session_state.error_message = None
                    st.session_state.call_start_time = None
                    
            except Exception as e:
                logger.error("Error ending call", exc_info=True)
                st.session_state.call_active = False
                st.session_state.call_status = "Ready"
                st.session_state.call_start_time = None

    def can_end_call(self):
        """Check if enough time has passed to enable end call button"""
        if not st.session_state.call_active or not st.session_state.call_start_time:
            return False
        
        time_elapsed = time.time() - st.session_state.call_start_time
        return time_elapsed >= 20  # Enable after 20 seconds

    def render_interface(self):
        st.set_page_config(
            page_title="JLP Voice Assistant",
            page_icon="🎯",
            layout="centered",
            initial_sidebar_state="collapsed"
        )
        
        self.add_custom_styles()

        # Main container
        st.markdown("""
            <div class="main-container">
                <h1 class="title">JLP Voice Assistant</h1>
                <div class="display-container">
                    <div class="phone-number">{}</div>
                </div>
            </div>
        """.format(st.session_state.phone_number), unsafe_allow_html=True)

        # Control buttons
        col1, col2 = st.columns(2)
        with col1:
            start_disabled = st.session_state.call_active or st.session_state.initializing
            if st.button(
                "🟢 Start Call", 
                key="start_call", 
                disabled=start_disabled,
                use_container_width=True
            ):
                self.start_call()

        with col2:
            if st.button(
                "🔴 End Call", 
                key="end_call", 
                use_container_width=True,
                type="primary"
            ):
                self.end_call()

        # Status indicator
        if st.session_state.initializing:
            st.markdown("""
                <div class="status-indicator">
                    🔄 Initializing AI Assistant...
                </div>
            """, unsafe_allow_html=True)
        elif st.session_state.call_active:
            if st.session_state.call_start_time:
                time_elapsed = time.time() - st.session_state.call_start_time
                if time_elapsed < 20:
                    status_text = f"🟢 Call in progress..."
                else:
                    status_text = "🟢 Call in progress..."
            else:
                status_text = "🟢 Call in progress..."
            
            st.markdown(f"""
                <div class="status-indicator">
                    {status_text}
                </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
                <div class="status-indicator">
                    ⚪ Ready to start
                </div>
            """, unsafe_allow_html=True)

    def add_custom_styles(self):
        st.markdown("""
            <style>
            .main-container {
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
                background-color: #1E1E1E;
                border-radius: 15px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            }
            
            .display-container {
                background-color: #2D2D2D;
                padding: 30px 20px;
                border-radius: 10px;
                text-align: center;
                margin: 20px 0;
            }
            
            .phone-number {
                font-size: 32px;
                font-weight: bold;
                color: #FFFFFF;
                font-family: 'Courier New', monospace;
                letter-spacing: 2px;
            }
            
            .title {
                text-align: center;
                font-size: 24px;
                color: #FFFFFF;
                margin-bottom: 20px;
                font-weight: 500;
            }
            
            .status-indicator {
                text-align: center;
                padding: 12px;
                margin: 20px 0;
                border-radius: 8px;
                font-weight: 500;
                background-color: #2D2D2D;
                color: #FFFFFF;
            }
            
            .stButton > button {
                width: 100% !important;
                padding: 15px 30px !important;
                border-radius: 8px !important;
                font-size: 16px !important;
                font-weight: 500 !important;
            }
            
            .start-button > button {
                background-color: #28a745 !important;
                color: white !important;
            }
            
            .end-button > button {
                background-color: #dc3545 !important;
                color: white !important;
            }
            </style>
        """, unsafe_allow_html=True)

def main():
    try:
        interface = CallInterface()
        interface.render_interface()
    except Exception as e:
        logger.error("Application Error", exc_info=True)

if __name__ == "__main__":
    main()