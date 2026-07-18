#!/bin/bash
# Deploy Boat Scraper to Docker Swarm via Portainer
set -e

# --- Configuration ---
REGISTRY="${REGISTRY:-localhost:5000}"
STACK_NAME="${STACK_NAME:-boat-scraper}"
IMAGE_TAG="${REGISTRY}/boat-scraper:latest"
NODE_HOSTNAME="${NODE_HOSTNAME:-}"

# --- 1. Build image ---
echo "=== Building image: ${IMAGE_TAG} ==="
docker build -t "${IMAGE_TAG}" .

# --- 2. Push to registry ---
echo "=== Pushing to registry ==="
docker push "${IMAGE_TAG}"

# --- 3. Handle data migration (if bind-mount option) ---
if [ -n "$NODE_HOSTNAME" ]; then
    echo "=== Preparing bind-mount on node: ${NODE_HOSTNAME} ==="
    # You'll need SSH access or be on the target node to copy data
    echo "Copy data/ directory to the target node at /var/lib/boat-scraper/data"
    echo "Then update compose.swarm.yaml with that path."
else
    echo "=== Named volume mode ==="
    echo "After deployment, copy your existing boats.db into the volume:"
    echo "  docker cp data/boats.db \$(docker ps -q -f name=boatscraper):/app/data/"
fi

# --- 4. Deploy via Portainer ---
echo ""
echo "=== Portainer Deployment ==="
echo "1. Open Portainer → Stacks → Add Stack"
echo "2. Name: ${STACK_NAME}"
echo "3. Build method: Repository OR Web editor"
echo "   If Repository: point to this repo, path: compose.swarm.yaml"
echo "   If Web editor: paste the contents of compose.swarm.yaml"
echo "4. Set environment variable: REGISTRY=${REGISTRY}"
echo "5. Deploy the stack"
echo ""
echo "=== Alternative: CLI deploy ==="
echo "  export REGISTRY=${REGISTRY}"
echo "  docker stack deploy -c compose.swarm.yaml ${STACK_NAME}"
