#!/bin/sh
# Generate gRPC/protobuf Python stubs from proto/agent.proto into app/transport/.
set -e
cd "$(dirname "$0")"
mkdir -p app/transport
python -m grpc_tools.protoc \
  -I proto \
  --python_out=app/transport \
  --grpc_python_out=app/transport \
  proto/agent.proto
# Fix absolute import to work inside the app.transport package
sed -i 's/^import agent_pb2 as agent__pb2$/from . import agent_pb2 as agent__pb2/' app/transport/agent_pb2_grpc.py
echo "generated app/transport/agent_pb2.py app/transport/agent_pb2_grpc.py"
