# Terraform: deploy to Cloud Run

Starter config. Read `main.tf` comments first; search for `TODO`.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars    # edit project_id
gcloud auth application-default login
terraform init
terraform apply -target=google_artifact_registry_repository.bot -target=google_secret_manager_secret.bot

# 1) add secret values (kept out of Terraform state)
printf '%s' 'xoxb-...' | gcloud secrets versions add slack-bot-token --data-file=-
printf '%s' 'xapp-...' | gcloud secrets versions add slack-app-token --data-file=-
printf '%s' 'none'     | gcloud secrets versions add llm-api-key     --data-file=-

# 2) build + push the image
gcloud auth configure-docker us-west1-docker.pkg.dev
docker build -t "$(terraform output -raw image)" .. && docker push "$(terraform output -raw image)"

# 3) deploy everything
terraform apply
gcloud run services logs read helpdesk-faq-bot --region us-west1 --limit 50

# done demoing? (min instances = 1 bills 24/7)
terraform destroy
```

If you `terraform apply` before the image exists, the Cloud Run step fails; that's why the
targeted apply comes first. Apple Silicon Macs: build with `docker build --platform linux/amd64`.
