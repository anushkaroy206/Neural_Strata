# Neural Strata: AI Mine Subsidence Early Warning System

A full-stack edge computing architecture designed to predict structural mine failures and orchestrate automated personnel evacuations using a digital twin dashboard. 

## System Architecture
*   **IoT Hardware Mesh:** Two ESP32 surface nodes read physical displacement (slide potentiometers) and tilt/acceleration (MPU-6050s), transmitting telemetry via the ESP-NOW protocol to an underground ESP32 Hub equipped with an RC522 RFID reader for personnel tracking.
*   **Edge Server (Python/Flask):** A lightweight backend reads the Hub's JSON serial stream, calculates derivative kinematics, logs historical data into PostgreSQL, and serves the REST API.
*   **AI Inference (XGBoost):** A pre-trained XGBoost regressor processes the live telemetry queues to forecast the exact Time-To-Failure (TTF) and breach risk, triggering automated evacuation protocols.
*   **Digital Twin Dashboard:** A zero-dependency HTML5/JS interface featuring a custom 3D cutaway engine, live-updating charts, and real-time Dijkstra shortest-path evacuation routing.

## Hardware Pinout Reference
*   **Surface Nodes (ESP32):** Potentiometer Signal -> `GPIO 34`. MPU-6050 SDA -> `GPIO 21`, SCL -> `GPIO 22`. Power via 3.3V/GND.
*   **Underground Hub (ESP32):** Connected to the edge server via USB for serial data transfer and power.
*   **RC522 RFID (SPI):** SDA -> `GPIO 5`, RST -> `GPIO 22`, MOSI -> `GPIO 23`, MISO -> `GPIO 19`, SCK -> `GPIO 18`.
*   **Power Warning:** RC522 VCC must strictly connect to `3.3V` to prevent chip burnout.

## Quick Start Guide
*   Flash the C++ firmware from the `/neuralstrata-nodes` directory to your ESP32 boards, ensuring the Hub's MAC address is synced across all files.
*   Set up the PostgreSQL database `neural_strata` with the `sensor_telemetry` and `rfid_evacuation_log` tables to enable persistent storage.
*   Install backend dependencies (`Flask`, `psycopg2`, `xgboost`, `pandas`) within your activated Python virtual environment.
*   Execute `python app.py` in the backend directory and open `http://localhost:5000` in a browser to launch the live dashboard.
