# Step 1: Base Image - Ek halka Python environment chunein
FROM python:3.10-slim

# Step 2: Working Directory - Container ke andar ek folder banayein
WORKDIR /app

# Step 3: Copy requirements file aur dependencies install karein
# Isse pehle copy karne se build speed fast hoti hai
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Step 4: Apne project ka code copy karein
COPY main.py .

# Step 5: Default Command - Batayein ki container start hone par kya run karna hai
CMD ["python", "main.py"]
