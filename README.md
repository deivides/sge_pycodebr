Inventory Management System (SGE)

Welcome to the Inventory Management System (SGE), a web application built with Django and Bootstrap to simplify stock management. This README provides essential information on how to set up and run the project locally using Docker or a traditional setup.
📌 Requirements

Make sure you have the following installed on your system:

    Python (Recommended version: 3.7 or higher)
    Docker & Docker Compose (if running with containers)
    Other dependencies listed in requirements.txt

⚙️ Setup & Installation
1️⃣ Running with Docker (Recommended)

To quickly set up and run the project using Docker, follow these steps:

    Clone the repository:

git clone https://github.com/your-username/inventory-management.git
cd inventory-management

Build and start the containers:

    docker-compose up --build

    Once the process completes, the application will be available at:
    👉 http://localhost:8000

2️⃣ Running Without Docker (Manual Setup)

If you prefer running the project manually, follow these steps:

    Create and activate a virtual environment:

python -m venv venv
source venv/bin/activate  # On macOS/Linux
venv\Scripts\activate     # On Windows

Install dependencies:

pip install -r requirements.txt

Apply database migrations:

python manage.py migrate

Start the development server:

    python manage.py runserver

    Access the system at:
    👉 http://localhost:8000

🛠️ Technologies Used

    Django – Backend framework for building web applications
    Bootstrap – Frontend framework for a responsive UI
    PostgreSQL / SQLite – Database (depending on configuration)
    Docker & Docker Compose – For containerized deployment
