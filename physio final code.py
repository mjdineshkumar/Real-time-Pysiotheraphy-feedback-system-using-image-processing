import cv2
import mediapipe as mp
import numpy as np
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk
from datetime import datetime
import time
import sys
import os
import wave
import subprocess
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from rpi_api_client import PhysioTrackerAPIClient
    API_AVAILABLE = True
except ImportError:
    API_AVAILABLE = False

try:
    from piper import PiperVoice
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False

API_BASE_URL = "http://fitness.appblocky.com/api"
USER_ID = 1
SEND_TO_API = False

DEMO_VIDEOS = {
    "Sit-ups": "/home/pi/Desktop/situps_video.mp4",
    "Squats": "/home/pi/videos/squats_demo.mp4",
    "Shoulder Abduction": "/home/pi/Desktop/situps_video.mp4",
    "Shoulder Flexion": "/home/pi/videos/shoulder_flexion_demo.mp4",
    "Elbow Extension": "/home/pi/videos/elbow_extension_demo.mp4",
}

VIDEO_SKIP_FRAMES = 2
VIDEO_TARGET_FPS = 15

PIPER_VOICE_MODEL = "en_US-lessac-medium.onnx"
AUDIO_TEMP_DIR = "/tmp/physio_audio"

mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles
pose = mp_pose.Pose(min_detection_confidence=0.5, min_tracking_confidence=0.5)

COLOR_BG = "#0F1419"
COLOR_ACCENT_GREEN = "#00D084"
COLOR_ACCENT_RED = "#FF4757"
COLOR_TEXT_PRIMARY = "#FFFFFF"
COLOR_TEXT_SECONDARY = "#A0A0A0"
COLOR_CARD_BG = "#1A1F2E"
COLOR_BORDER = "#2A3142"

def calculate_angle(a, b, c):
    a, b, c = np.array(a), np.array(b), np.array(c)
    radians = np.arctan2(c[1]-b[1], c[0]-b[0]) - np.arctan2(a[1]-b[1], a[0]-b[0])
    angle = np.abs(radians*180.0/np.pi)
    return 360-angle if angle > 180.0 else angle

class PiperTTS:
    def __init__(self, voice_model=PIPER_VOICE_MODEL):
        self.voice_model = voice_model
        self.voice = None
        self.enabled = False
        
        if not os.path.exists(AUDIO_TEMP_DIR):
            os.makedirs(AUDIO_TEMP_DIR)
        
        if PIPER_AVAILABLE:
            self.load_voice()
    
    def load_voice(self):
        try:
            self.voice = PiperVoice.load(self.voice_model)
            self.enabled = True
        except Exception as e:
            self.enabled = False
    
    def speak(self, text):
        if not self.enabled or not self.voice:
            return False
        
        threading.Thread(target=self._play_audio, args=(text,), daemon=True).start()
        return True
    
    def _play_audio(self, text):
        try:
            audio_file = f"{AUDIO_TEMP_DIR}/speech_{int(time.time()*1000)}.wav"
            
            with wave.open(audio_file, "wb") as wav_file:
                self.voice.synthesize_wav(text, wav_file)
            
            subprocess.Popen(['aplay', audio_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            threading.Timer(10.0, lambda: self._cleanup_audio(audio_file)).start()
        except Exception as e:
            pass
    
    def _cleanup_audio(self, audio_file):
        try:
            if os.path.exists(audio_file):
                os.remove(audio_file)
        except:
            pass

class PhysioSession:
    def __init__(self):
        self.exercise = ""
        self.target_reps = 0
        self.good_reps = 0
        self.total_attempts = 0
        self.is_correct = False
        self.current_angle = 0
        self.start_time = None
        self.last_form_feedback = 0
        self.last_rep_count = 0

    def reset(self, name, reps):
        self.exercise = name
        self.target_reps = int(reps)
        self.good_reps = 0
        self.total_attempts = 0
        self.is_correct = False
        self.current_angle = 0
        self.start_time = time.time()
        self.last_form_feedback = 0
        self.last_rep_count = 0

    def get_accuracy(self):
        if self.total_attempts == 0:
            return 0
        return (self.good_reps / self.total_attempts) * 100
    
    def get_duration(self):
        if self.start_time is None:
            return 0
        return int(time.time() - self.start_time)

session = PhysioSession()

class SimpleDemoVideoPlayer:
    def __init__(self, video_path, width=280, height=340, skip_frames=2):
        self.video_path = video_path
        self.width = width
        self.height = height
        self.skip_frames = skip_frames
        self.cap = None
        self.is_loaded = False
        self.current_frame = None
        self.total_frames = 0
        
        self.load_video()
    
    def load_video(self):
        if not os.path.exists(self.video_path):
            return False
        
        try:
            self.cap = cv2.VideoCapture(self.video_path)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            if not self.cap.isOpened():
                return False
            
            self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.is_loaded = True
            return True
        except:
            return False
    
    def get_next_frame(self):
        if not self.cap or not self.is_loaded:
            return None
        
        try:
            for _ in range(self.skip_frames):
                ret, frame = self.cap.read()
                
                if not ret:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = self.cap.read()
                    if not ret:
                        return None
            
            if ret:
                frame = cv2.resize(frame, (self.width, self.height))
                self.current_frame = frame
                return frame
            
            return None
        except:
            return None
    
    def reset(self):
        if self.cap:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    
    def release(self):
        if self.cap:
            self.cap.release()
            self.cap = None
            self.is_loaded = False

class PhysioUI:
    def __init__(self, root):
        self.root = root
        self.root.title("PhysioTracker - Audio Feedback")
        self.root.geometry("1000x600")
        self.root.configure(bg=COLOR_BG)
        
        self.cap = None
        self.video_running = False
        self.session_active = False
        self.photo_image = None
        self.demo_video = None
        self.demo_photo = None
        
        self.tts = PiperTTS()
        
        self.api_client = None
        if SEND_TO_API and API_AVAILABLE:
            self.api_client = PhysioTrackerAPIClient(API_BASE_URL, USER_ID)
        
        self.create_ui()

    def create_ui(self):
        main_frame = tk.Frame(self.root, bg=COLOR_BG)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        header_frame = tk.Frame(main_frame, bg=COLOR_CARD_BG, height=40)
        header_frame.pack(fill=tk.X, padx=0, pady=0)
        header_frame.pack_propagate(False)
        
        tk.Label(header_frame, text="PhysioTracker - Audio Feedback", 
                font=("Helvetica", 14, "bold"), fg=COLOR_ACCENT_GREEN, bg=COLOR_CARD_BG).pack(anchor="w", padx=15, pady=6)
        
        content_frame = tk.Frame(main_frame, bg=COLOR_BG)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        left_frame = tk.Frame(content_frame, bg=COLOR_CARD_BG, relief=tk.FLAT, borderwidth=1)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 3))
        
        tk.Label(left_frame, text="YOUR POSE", font=("Helvetica", 9, "bold"), 
                fg=COLOR_ACCENT_GREEN, bg=COLOR_CARD_BG).pack(anchor="nw", padx=6, pady=4)
        
        self.camera_canvas = tk.Canvas(left_frame, bg=COLOR_CARD_BG, highlightthickness=0, 
                                       width=280, height=340)
        self.camera_canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.camera_canvas.create_text(140, 170, text="START", 
                                      fill=COLOR_TEXT_SECONDARY, font=("Helvetica", 10))
        
        center_frame = tk.Frame(content_frame, bg=COLOR_CARD_BG, relief=tk.FLAT, borderwidth=1)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=3)
        
        tk.Label(center_frame, text="DEMO VIDEO", font=("Helvetica", 9, "bold"), 
                fg=COLOR_ACCENT_GREEN, bg=COLOR_CARD_BG).pack(anchor="nw", padx=6, pady=4)
        
        self.demo_canvas = tk.Canvas(center_frame, bg=COLOR_CARD_BG, highlightthickness=0, 
                                     width=280, height=340)
        self.demo_canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.demo_canvas.create_text(140, 170, text="Select exercise", 
                                    fill=COLOR_TEXT_SECONDARY, font=("Helvetica", 9))
        
        right_frame = tk.Frame(content_frame, bg=COLOR_CARD_BG, relief=tk.FLAT, borderwidth=1, width=180)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False, padx=(3, 0))
        right_frame.pack_propagate(False)
        
        tk.Label(right_frame, text="STATS", font=("Helvetica", 9, "bold"), 
                fg=COLOR_ACCENT_GREEN, bg=COLOR_CARD_BG).pack(anchor="nw", padx=6, pady=4)
        
        self.stats_canvas = tk.Canvas(right_frame, bg=COLOR_CARD_BG, highlightthickness=0)
        self.stats_canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        
        self.update_stats_display()
        
        ctrl_frame = tk.Frame(main_frame, bg=COLOR_BG, height=100)
        ctrl_frame.pack(fill=tk.X, padx=5, pady=(0, 5))
        ctrl_frame.pack_propagate(False)
        
        row1 = tk.Frame(ctrl_frame, bg=COLOR_BG)
        row1.pack(fill=tk.X, pady=2)
        
        tk.Label(row1, text="Ex:", font=("Helvetica", 8, "bold"), 
                fg=COLOR_TEXT_PRIMARY, bg=COLOR_BG, width=4).pack(side=tk.LEFT, padx=2)
        
        self.selected_ex = tk.StringVar(value="Sit-ups")
        self.selected_ex.trace('w', self.on_exercise_changed)
        
        ex_menu = tk.OptionMenu(row1, self.selected_ex, 
                               "Sit-ups", "Squats", "Shoulder Abd.", "Shoulder Flex.", "Elbow Ext.")
        ex_menu.config(bg=COLOR_CARD_BG, fg=COLOR_TEXT_PRIMARY, font=("Helvetica", 8), width=18)
        ex_menu["menu"].config(bg=COLOR_CARD_BG, fg=COLOR_TEXT_PRIMARY, font=("Helvetica", 8))
        ex_menu.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)
        
        tk.Label(row1, text="Reps:", font=("Helvetica", 8, "bold"), 
                fg=COLOR_TEXT_PRIMARY, bg=COLOR_BG).pack(side=tk.LEFT, padx=2)
        
        self.rep_input = tk.Entry(row1, font=("Helvetica", 9), width=3,
                                 bg=COLOR_CARD_BG, fg=COLOR_ACCENT_GREEN, insertbackground=COLOR_ACCENT_GREEN)
        self.rep_input.insert(0, "5")
        self.rep_input.pack(side=tk.LEFT, padx=2)
        
        row2 = tk.Frame(ctrl_frame, bg=COLOR_BG)
        row2.pack(fill=tk.X, pady=2)
        
        self.start_btn = tk.Button(row2, text="START", command=self.start_session,
                                  bg=COLOR_ACCENT_GREEN, fg=COLOR_BG, font=("Helvetica", 8, "bold"),
                                  padx=10, pady=4, relief=tk.FLAT, width=10)
        self.start_btn.pack(side=tk.LEFT, padx=2)
        
        self.stop_btn = tk.Button(row2, text="STOP", command=self.stop_session,
                                 bg=COLOR_ACCENT_RED, fg=COLOR_BG, font=("Helvetica", 8, "bold"),
                                 padx=10, pady=4, relief=tk.FLAT, width=10, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=2)
        
        self.status_label = tk.Label(row2, text="Ready", font=("Helvetica", 8), 
                                    fg=COLOR_TEXT_SECONDARY, bg=COLOR_BG)
        self.status_label.pack(side=tk.LEFT, padx=10)
        
        audio_status = "ON" if self.tts.enabled else "OFF"
        audio_color = COLOR_ACCENT_GREEN if self.tts.enabled else COLOR_ACCENT_RED
        tk.Label(row2, text=f"Audio: {audio_status}", font=("Helvetica", 7), 
                fg=audio_color, bg=COLOR_BG).pack(side=tk.RIGHT, padx=10)

    def on_exercise_changed(self, *args):
        if not self.session_active:
            self.load_demo_video()

    def load_demo_video(self):
        ex_map = {
            "Sit-ups": "Sit-ups",
            "Squats": "Squats",
            "Shoulder Abd.": "Shoulder Abduction",
            "Shoulder Flex.": "Shoulder Flexion",
            "Elbow Ext.": "Elbow Extension",
        }
        
        exercise_name = ex_map.get(self.selected_ex.get(), self.selected_ex.get())
        video_path = DEMO_VIDEOS.get(exercise_name)
        
        if self.demo_video:
            self.demo_video.release()
        
        if video_path and os.path.exists(video_path):
            self.demo_video = SimpleDemoVideoPlayer(
                video_path, 
                width=280, 
                height=340,
                skip_frames=VIDEO_SKIP_FRAMES
            )
            frame = self.demo_video.get_next_frame()
            if frame is not None:
                self.display_demo_frame(frame)
        else:
            self.demo_canvas.delete("all")
            self.demo_canvas.create_text(140, 170, text="No demo video", 
                                        fill=COLOR_TEXT_SECONDARY, font=("Helvetica", 9))

    def display_demo_frame(self, frame):
        try:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_image = Image.fromarray(frame_rgb)
            photo = ImageTk.PhotoImage(pil_image)
            
            self.demo_canvas.delete("all")
            self.demo_canvas.create_image(140, 170, image=photo)
            self.demo_photo = photo
        except:
            pass

    def update_stats_display(self):
        self.stats_canvas.delete("all")
        
        y = 15
        font_label = ("Helvetica", 7)
        font_value = ("Helvetica", 14, "bold")
        
        stats = [
            ("Reps", f"{session.good_reps}/{session.target_reps}", COLOR_ACCENT_GREEN, 35),
            ("Acc", f"{session.get_accuracy():.0f}%", COLOR_ACCENT_GREEN if session.get_accuracy() > 70 else COLOR_TEXT_SECONDARY, 35),
            ("Ang", f"{session.current_angle:.0f}°", COLOR_TEXT_SECONDARY, 35),
            ("Stat", "OK" if session.is_correct else "ADJ", COLOR_ACCENT_GREEN if session.is_correct else COLOR_ACCENT_RED, 35),
        ]
        
        for label, value, color, height in stats:
            self.stats_canvas.create_text(8, y, text=label, font=font_label, fill=COLOR_TEXT_SECONDARY, anchor="nw")
            self.stats_canvas.create_text(8, y+12, text=value, font=font_value, fill=color, anchor="nw")
            self.stats_canvas.create_line(5, y+32, 170, y+32, fill=COLOR_BORDER, width=1)
            y += height

    def start_session(self):
        if not self.selected_ex.get() or not self.rep_input.get().isdigit():
            messagebox.showwarning("Error", "Select exercise and reps")
            return
        
        ex_map = {
            "Sit-ups": "Sit-ups",
            "Squats": "Squats",
            "Shoulder Abd.": "Shoulder Abduction",
            "Shoulder Flex.": "Shoulder Flexion",
            "Elbow Ext.": "Elbow Extension",
        }
        
        full_ex_name = ex_map.get(self.selected_ex.get(), self.selected_ex.get())
        session.reset(full_ex_name, self.rep_input.get())
        
        self.session_active = True
        self.video_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_label.config(text="Running...", fg=COLOR_ACCENT_GREEN)
        
        self.tts.speak(f"Starting {session.exercise}. Get ready!")
        
        if self.api_client:
            if not self.api_client.health_check():
                self.api_client = None
        
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            messagebox.showerror("Error", "Camera not found")
            self.video_running = False
            self.session_active = False
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            return
        
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 15)
        
        self.run_exercise_loop()

    def stop_session(self):
        self.video_running = False
        self.session_active = False
        if self.cap:
            self.cap.release()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_label.config(text="Stopped", fg=COLOR_TEXT_SECONDARY)
        self.tts.speak("Session stopped")

    def run_exercise_loop(self):
        counter = 0
        stage = None
        frame_count = 0
        last_demo_update = time.time()
        
        while self.video_running and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                break
            
            frame = cv2.flip(frame, 1)
            
            image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = pose.process(image)
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            h, w, _ = image.shape
            
            if results.pose_landmarks:
                mp_drawing.draw_landmarks(image, results.pose_landmarks, mp_pose.POSE_CONNECTIONS,
                                        landmark_drawing_spec=mp_drawing_styles.get_default_pose_landmarks_style())
                
                try:
                    lm = results.pose_landmarks.landmark
                    angle = 0
                    p1, p2, p3 = [0, 0], [0, 0], [0, 0]
                    
                    if session.exercise == "Sit-ups":
                        p1, p2, p3 = [lm[11].x, lm[11].y], [lm[23].x, lm[23].y], [lm[25].x, lm[25].y]
                        angle = calculate_angle(p1, p2, p3)
                        if angle > 110: stage = "down"
                        if angle < 65 and stage == "down":
                            stage, counter = "up", counter + 1
                            session.good_reps += 1
                            session.total_attempts += 1
                            self.tts.speak(f"Rep {session.good_reps}")
                        session.is_correct = (angle < 70)
                        
                        if not session.is_correct and time.time() - session.last_form_feedback > 3:
                            self.tts.speak("Improve your form")
                            session.last_form_feedback = time.time()
                    
                    elif session.exercise == "Squats":
                        p1, p2, p3 = [lm[23].x, lm[23].y], [lm[25].x, lm[25].y], [lm[27].x, lm[27].y]
                        angle = calculate_angle(p1, p2, p3)
                        if angle > 160: stage = "up"
                        if angle < 95 and stage == "up":
                            stage, counter = "down", counter + 1
                            session.good_reps += 1
                            session.total_attempts += 1
                            self.tts.speak(f"Rep {session.good_reps}")
                        session.is_correct = (angle < 100)
                        
                        if not session.is_correct and time.time() - session.last_form_feedback > 3:
                            self.tts.speak("Go deeper")
                            session.last_form_feedback = time.time()
                    
                    elif session.exercise == "Shoulder Abduction":
                        p1, p2, p3 = [lm[23].x, lm[23].y], [lm[11].x, lm[11].y], [lm[13].x, lm[13].y]
                        angle = calculate_angle(p1, p2, p3)
                        if angle < 30: stage = "down"
                        if angle > 140 and stage == "down":
                            stage, counter = "up", counter + 1
                            session.good_reps += 1
                            session.total_attempts += 1
                            self.tts.speak(f"Rep {session.good_reps}")
                        session.is_correct = (angle > 140)
                        
                        if not session.is_correct and time.time() - session.last_form_feedback > 3:
                            self.tts.speak("Raise your arm higher")
                            session.last_form_feedback = time.time()
                    
                    elif session.exercise == "Elbow Extension":
                        p1, p2, p3 = [lm[11].x, lm[11].y], [lm[13].x, lm[13].y], [lm[15].x, lm[15].y]
                        angle = calculate_angle(p1, p2, p3)
                        if angle < 90: stage = "bent"
                        if angle > 160 and stage == "bent":
                            stage, counter = "straight", counter + 1
                            session.good_reps += 1
                            session.total_attempts += 1
                            self.tts.speak(f"Rep {session.good_reps}")
                        session.is_correct = (angle > 160)
                        
                        if not session.is_correct and time.time() - session.last_form_feedback > 3:
                            self.tts.speak("Straighten your arm completely")
                            session.last_form_feedback = time.time()
                    
                    elif session.exercise == "Shoulder Flexion":
                        p1, p2, p3 = [lm[23].x, lm[23].y], [lm[11].x, lm[11].y], [lm[15].x, lm[15].y]
                        angle = calculate_angle(p1, p2, p3)
                        if angle < 30: stage = "down"
                        if angle > 150 and stage == "down":
                            stage, counter = "up", counter + 1
                            session.good_reps += 1
                            session.total_attempts += 1
                            self.tts.speak(f"Rep {session.good_reps}")
                        session.is_correct = (angle > 150)
                        
                        if not session.is_correct and time.time() - session.last_form_feedback > 3:
                            self.tts.speak("Lift your arm higher")
                            session.last_form_feedback = time.time()
                    
                    session.current_angle = angle
                    
                    joint_coord = tuple(np.multiply(p2, [w, h]).astype(int))
                    cv2.putText(image, f"{int(angle)}", joint_coord, cv2.FONT_HERSHEY_SIMPLEX, 
                               0.8, (0, 255, 200), 1)
                
                except:
                    pass
            
            status_color = (0, 255, 0) if session.is_correct else (0, 0, 255)
            cv2.rectangle(image, (5, 5), (220, 70), (30, 30, 30), -1)
            cv2.putText(image, f"Rep: {session.good_reps}/{session.target_reps}",
                       (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 200), 1)
            cv2.circle(image, (200, 25), 10, status_color, -1)
            
            if frame_count % 2 == 0:
                try:
                    self.display_camera_frame(image)
                    self.update_stats_display()
                    
                    current_time = time.time()
                    if (current_time - last_demo_update) >= (1.0 / VIDEO_TARGET_FPS):
                        if self.demo_video:
                            demo_frame = self.demo_video.get_next_frame()
                            if demo_frame is not None:
                                self.display_demo_frame(demo_frame)
                        last_demo_update = current_time
                except:
                    pass
            
            frame_count += 1
            
            if session.good_reps >= session.target_reps:
                self.tts.speak("Congratulations! You completed all reps!")
                break
            
            self.root.update()
            time.sleep(0.01)
        
        if self.cap:
            self.cap.release()
        if self.demo_video:
            self.demo_video.release()
        
        self.video_running = False
        self.session_active = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        
        duration = session.get_duration()
        accuracy = session.get_accuracy()
        
        if self.api_client and SEND_TO_API:
            success = self.api_client.save_session(
                exercise=session.exercise,
                target_reps=session.target_reps,
                completed_reps=session.good_reps,
                total_attempts=session.total_attempts,
                accuracy=accuracy,
                duration=duration,
                notes=f"Raspberry Pi - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
            
            if success:
                report = f"{session.exercise}\nReps: {session.good_reps}/{session.target_reps}\nAccuracy: {accuracy:.1f}%\nSaved to server"
            else:
                report = f"{session.exercise}\nReps: {session.good_reps}/{session.target_reps}\nAccuracy: {accuracy:.1f}%"
        else:
            report = f"{session.exercise}\nReps: {session.good_reps}/{session.target_reps}\nAccuracy: {accuracy:.1f}%"
        
        messagebox.showinfo("Complete", report)
        self.status_label.config(text="Ready", fg=COLOR_TEXT_SECONDARY)
        self.load_demo_video()

    def display_camera_frame(self, frame):
        try:
            frame_resized = cv2.resize(frame, (280, 340))
            frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)
            
            pil_image = Image.fromarray(frame_rgb)
            photo = ImageTk.PhotoImage(pil_image)
            
            self.camera_canvas.delete("all")
            self.camera_canvas.create_image(140, 170, image=photo)
            self.photo_image = photo
            
        except:
            pass

if __name__ == "__main__":
    try:
        root = tk.Tk()
        ui = PhysioUI(root)
        root.mainloop()
    except Exception as e:
        pass
