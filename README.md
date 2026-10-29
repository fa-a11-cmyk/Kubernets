# Microservices sur Kubernetes

Projet de démonstration local : une interface Nginx, une API FastAPI, PostgreSQL avec volume persistant, Redis, Ingress NGINX, Helm, GitHub Actions, Prometheus/Grafana et Loki.

## Architecture

| Service | Rôle |
| --- | --- |
| `frontend` | Page statique servie par Nginx non privilégié |
| `api` | API FastAPI, métriques Prometheus sur `/metrics` |
| `postgres` | PostgreSQL en StatefulSet avec PVC |
| `redis` | Cache des lectures de `/items` |
| Ingress NGINX | Route `/` vers l’interface et `/api/*` vers l’API |
| Helm | Chart dans `helm/microservices`, avec profils dev et prod |
| kube-prometheus-stack | Prometheus, Grafana, ServiceMonitor et tableau de bord |
| Loki + Alloy | Agrégation et collecte des journaux des pods |

L’API expose `GET /health`, `GET /health/live`, `GET /health/ready`, `GET /items`, `POST /items` et `/metrics`. Les probes Kubernetes utilisent une vérification de vie indépendante des dépendances et une vérification de disponibilité qui contrôle PostgreSQL et Redis. La table est initialisée au démarrage pour garder le projet facile à lancer ; pour une application réelle, remplacez cette initialisation par des migrations versionnées.

## Lancer sans Kubernetes

Prérequis : Docker Desktop ou Docker Engine avec Docker Compose.

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Ouvrez <http://localhost:8080>. PostgreSQL conserve ses données dans le volume `postgres_data`. Le mot de passe de `.env.example` est réservé à cet exemple local.

## Déployer sur Minikube

Prérequis : Docker, Minikube, `kubectl` et Helm 3. Le cluster local doit disposer d’environ 4 CPU et 8 Go de mémoire si vous installez aussi la pile de monitoring.

```powershell
minikube start --driver=docker --cpus=4 --memory=8192
minikube addons enable ingress
minikube addons enable metrics-server
```

Installez la pile d’observabilité avant l’application afin que le CRD `ServiceMonitor` soit disponible :

```powershell
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana-community https://grafana-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm upgrade --install monitoring prometheus-community/kube-prometheus-stack `
  --namespace observability --create-namespace `
  --values monitoring/prometheus-values.yaml

helm upgrade --install loki grafana-community/loki `
  --namespace observability `
  --values monitoring/loki-values.yaml

helm upgrade --install alloy grafana/alloy `
  --namespace observability `
  --values monitoring/alloy-values.yaml
```

Le profil Loki fourni est pour un cluster de développement à un nœud, avec stockage local sur PVC et authentification désactivée. Pour la production, configurez un stockage objet et l’authentification. La procédure suit les [instructions Helm officielles de Loki](https://grafana.com/docs/loki/latest/setup/install/helm/) et l’exemple officiel Alloy pour expédier les logs Kubernetes vers Loki.

Construisez les images directement dans Minikube, pour qu’il puisse les démarrer sans les publier :

```powershell
minikube image build -t microservices-api:dev ./api
minikube image build -t microservices-frontend:dev ./frontend
```

Installez l’application. Remplacez le mot de passe par une valeur locale à vous :

```powershell
helm upgrade --install demo ./helm/microservices `
  --namespace microservices --create-namespace `
  --values ./helm/microservices/values.yaml `
  --values ./helm/microservices/values-dev.yaml `
  --set-string postgresql.password='change-this-local-password' `
  --set monitoring.serviceMonitor.enabled=true `
  --wait --timeout 5m
```

Vérifiez les ressources :

```powershell
kubectl get pods,svc,ingress,hpa,pvc -n microservices
```

Pour un accès direct sans modifier le fichier hosts, ouvrez un autre terminal puis lancez :

```powershell
kubectl port-forward -n microservices svc/demo-microservices-frontend 8080:8080
```

Ouvrez <http://localhost:8080>. Pour tester l’Ingress, associez `microservices.local` à l’adresse renvoyée par `minikube ip` dans le fichier Windows `C:\Windows\System32\drivers\etc\hosts`, puis ouvrez <http://microservices.local>.

L’API se teste avec ces commandes :

```powershell
curl.exe http://localhost:8080/api/health
curl.exe http://localhost:8080/api/items
curl.exe -X POST http://localhost:8080/api/items `
  -H "Content-Type: application/json" `
  -d '{"name":"Premier item","description":"Créé via l’API"}'
```

## CI/CD GitHub Actions

Le workflow `.github/workflows/ci-cd.yml` lance les tests API sur les pull requests et les pushs, construit les deux images avec BuildKit, les publie sur GitHub Container Registry (`ghcr.io`) avec le SHA Git comme tag et déclenche le déploiement Helm après un push sur `main`. La publication utilise le `GITHUB_TOKEN` intégré à Actions ; aucun secret Docker Hub n'est nécessaire.

Ajoutez ce secret au dépôt GitHub pour le déploiement local :

- `POSTGRES_PASSWORD` (valeur forte réservée à cet environnement)

Après la première publication, rendez publics les deux packages `microservices-api` et `microservices-frontend` dans **GitHub > Packages > Package settings > Change visibility** pour que le cluster puisse télécharger les images sans identifiants. Le déploiement local est désactivé par défaut : activez la variable de dépôt `DEPLOY_LOCAL` avec la valeur `true` uniquement après avoir rendu les packages publics, configuré le secret `POSTGRES_PASSWORD` et installé un runner Linux auto-hébergé avec les labels `self-hosted`, `linux` et `kubernetes`. Ce runner doit avoir Docker, Helm, `kubectl`, l’accès réseau au cluster Minikube/kind et un `KUBECONFIG` limité au namespace `microservices`. Un runner GitHub hébergé n’a pas automatiquement accès au cluster local ; GitHub documente les options d’accès aux réseaux privés si vous souhaitez utiliser un tunnel à la place d’un runner local ([runners auto-hébergés](https://docs.github.com/en/actions/concepts/runners/self-hosted-runners), [réseau privé pour runners hébergés](https://docs.github.com/en/actions/how-tos/manage-runners/github-hosted-runners/connect-to-a-private-network)).

Pour initialiser le namespace et le rôle RBAC de déploiement depuis un contexte administrateur :

```powershell
kubectl create namespace microservices
kubectl apply -f deploy/rbac/deployer.yaml
```

Le rôle ne donne des droits que dans `microservices` et inclut les secrets nécessaires au stockage de l’état Helm. Configurez l’identité du runner sur ce rôle ; ne placez pas de kubeconfig administrateur dans les secrets GitHub.

## Monitoring et logs

- Le ServiceMonitor `demo-microservices-api` collecte `/metrics` toutes les 30 secondes. Il est activé par la commande d’installation et le label `release: monitoring` correspond à la release Prometheus `monitoring`.
- Le tableau Grafana est chargé depuis le ConfigMap créé par le chart. Il affiche le débit des requêtes, la latence p95 et les codes de réponse.
- Alloy collecte les logs des pods Kubernetes et les expédie à Loki. Dans Grafana, ouvrez **Explore**, sélectionnez **Loki** et utilisez `{namespace="microservices"}`.
- Pour ouvrir Grafana :

```powershell
kubectl port-forward -n observability svc/monitoring-grafana 3000:80
```

Identifiant par défaut : `admin`. Pour lire le mot de passe dans PowerShell :

```powershell
$encoded = kubectl get secret -n observability monitoring-grafana -o jsonpath='{.data.admin-password}'
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
```

Grafana est disponible sur <http://localhost:3000>.

## Dépannage

```powershell
kubectl get pods -n microservices
kubectl describe pod <nom-du-pod> -n microservices
kubectl logs deployment/demo-microservices-api -n microservices
kubectl logs deployment/demo-microservices-api -n microservices --previous
kubectl get events -n microservices --sort-by=.metadata.creationTimestamp
kubectl get pvc -n microservices
kubectl top pods -n microservices
```

Si le HPA n’affiche pas de métriques, vérifiez que `metrics-server` est activé et que les conteneurs ont une demande CPU. Si les pods API ne sont pas prêts, inspectez PostgreSQL, Redis et les logs de l’API ; `/health/ready` vérifie les deux dépendances. Un PVC `Pending` indique généralement qu’aucune StorageClass par défaut n’est disponible.

### Exercice facultatif : CrashLoopBackOff et OOMKilled

À faire uniquement sur le cluster de démonstration :

1. Pour observer un `CrashLoopBackOff`, remplacez temporairement l’image de l’API par une image qui se termine immédiatement : `kubectl set image deployment/demo-microservices-api api=busybox:1.36 -n microservices`.
2. Relevez l’état avec `kubectl get pods -n microservices`, puis `kubectl describe pod <pod> -n microservices` et les événements. Revenez à l’image précédente avec `kubectl rollout undo deployment/demo-microservices-api -n microservices`.
3. Pour observer `OOMKilled`, abaissez temporairement la limite mémoire API, par exemple `kubectl set resources deployment/demo-microservices-api -n microservices -c api --limits=memory=16Mi`, puis observez `kubectl describe pod` et `kubectl logs <pod> -n microservices --previous`.
4. Restaurez la limite précédente avec `kubectl rollout undo deployment/demo-microservices-api -n microservices`. Notez le symptôme, les commandes d’investigation, la cause trouvée et la correction dans votre compte rendu.

## Profils Helm

- `helm/microservices/values-dev.yaml` utilise les images locales Minikube, une seule réplique API et un HPA réduit.
- `helm/microservices/values-prod.yaml` est un exemple de dimensionnement ; il référence un Secret externe `app-postgresql` et des images à remplacer par votre CI. Ne versionnez aucun secret réel dans ces fichiers.

Le chart est dans `helm/microservices`. La commande suivante permet d’inspecter les manifests Kubernetes générés sans les appliquer :

```powershell
helm template demo ./helm/microservices --namespace microservices `
  --set-string postgresql.password='local-template-only'
```
