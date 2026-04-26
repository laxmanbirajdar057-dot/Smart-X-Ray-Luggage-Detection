# 🛡️ Smart X-Ray Luggage Detection System

An AI-enhanced security system that automatically detects prohibited and suspicious items in X-ray luggage scans using computer vision and deep learning — with a real-time web dashboard and hardware integration.

---

## 📌 Overview

Traditional luggage screening relies entirely on human operators, leading to fatigue-related errors and inconsistent detection. This system augments human operators with an AI model that analyzes X-ray scan images in real time, flags threats, and streams alerts to a live dashboard — reducing response time and improving detection accuracy.

---

## 🏗️ Project Structure

```
Smart-X-Ray-Luggage-Detection/
├── AI/              # Python-based detection model (YOLO / image classification)
├── Backend/         # Spring Boot REST API + WebSocket + JWT Auth
├── Frontend/        # Real-time web dashboard (HTML/CSS/JS)
├── assets/          # Hardware setup photos and demo screenshots
└── .gitignore
```

---

## ⚙️ Tech Stack

| Layer      | Technology                                      |
|------------|-------------------------------------------------|
| AI Model   | Python, OpenCV, YOLOv8 / TensorFlow             |
| Backend    | Java 17, Spring Boot 3.2, Spring Security, JWT  |
| Database   | MySQL, Spring Data JPA / Hibernate              |
| API        | REST APIs, WebSocket (real-time alerts)         |
| Frontend   | HTML, CSS, JavaScript                           |
| Build Tool | Maven                                           |
| Hardware   | X-Ray conveyor belt system + camera integration |

---

## ✨ Features

- 🔍 **AI-powered detection** — identifies prohibited items (weapons, sharp objects, liquids) in X-ray scans
- ⚡ **Real-time alerts** — WebSocket-based live notifications to the operator dashboard
- 🔐 **Secure access** — JWT-based authentication for operator login
- 📊 **Dashboard** — live scan feed, alert history, and system status
- ⚙️ **System settings** — configurable detection thresholds via admin panel
- 🖥️ **Hardware integration** — connected to physical X-ray conveyor belt system

---

## 🚀 Getting Started

### Prerequisites
- Java 17+
- Maven 3.8+
- MySQL 8+
- Python 3.9+ (for AI module)

### Backend Setup

```bash
cd Backend

# Configure database in src/main/resources/application.properties
# spring.datasource.url=jdbc:mysql://localhost:3306/xrayluggage
# spring.datasource.username=your_username
# spring.datasource.password=your_password

mvn clean install
mvn spring-boot:run
```

### AI Module Setup

```bash
cd AI
pip install -r requirements.txt
python detect.py
```

### Frontend
Open `Frontend/index.html` in your browser, or serve it via Live Server.

---

## 📸 Hardware Setup

> Real-world deployment on X-ray luggage screening hardware.

<!-- Add your photos here -->
<!-- ![Hardware Setup](assets/hardware1.jpg) -->
<!-- ![Detection in Action](assets/hardware2.jpg) -->

*(Hardware photos coming soon)*

---

## 🔌 API Endpoints

| Method | Endpoint              | Description              |
|--------|-----------------------|--------------------------|
| POST   | `/api/auth/login`     | Operator login (JWT)     |
| GET    | `/api/scans`          | Get all scan records     |
| POST   | `/api/scans/analyze`  | Submit image for analysis|
| GET    | `/api/settings`       | Get system settings      |
| PUT    | `/api/settings`       | Update system settings   |
| WS     | `/ws/alerts`          | Real-time alert stream   |

---

## 👨‍💻 Author

**Laxman Birajdar**  
Electronics & Telecom Engineering | Java Backend Developer  
[GitHub](https://github.com/laxmanbirajdar057-dot) • [LinkedIn](https://linkedin.com/in/your-profile)

---

## 📄 License

This project is for academic and portfolio purposes.
