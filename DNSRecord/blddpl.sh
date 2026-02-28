docker build -t docker.io/kigarsk/dns-operator:0.1.0 .
docker push kigarsk/dns-operator:0.1.0 
kubectl delete -f manifests/deployment.yaml 
kubectl apply -f manifests/deployment.yaml
