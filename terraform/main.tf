# Deploys the bot to Cloud Run with tokens in Secret Manager.
#
# Why Cloud Run works for a Slack bot with NO public URL:
#   Socket Mode opens an OUTBOUND websocket to Slack, so nothing needs to reach the container.
#   We still listen on :8000 (Prometheus /metrics) because Cloud Run requires a listening port,
#   keep exactly one instance warm, and disable CPU throttling so the websocket stays alive.
#
# Alternatives (TODO, pick one later):
#   * Cloud Run worker pools - made for non-HTTP background workers; check the google provider
#     docs for `google_cloud_run_v2_worker_pool` before switching.
#   * GKE Autopilot - a 1-replica Deployment + ServiceMonitor; more to manage, but reuses the
#     bootcamp's K8s/Prometheus stack. See README "Roadmap".
#   * A small e2-micro VM running docker compose (cheapest always-on option).
#
# Cost note: min_instance_count = 1 with always-on CPU is billed 24/7 (a few $/month at
# 0.25-0.5 vCPU, but check the pricing calculator). Run `terraform destroy` when done demoing.

locals {
  image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.bot.repository_id}/${var.service_name}:${var.image_tag}"

  # Secrets the container needs. Values are added OUTSIDE Terraform so they never land in state:
  #   printf '%s' 'xoxb-...' | gcloud secrets versions add slack-bot-token --data-file=-
  secrets = {
    SLACK_BOT_TOKEN = "slack-bot-token"
    SLACK_APP_TOKEN = "slack-app-token"
    LLM_API_KEY     = "llm-api-key" # add a placeholder version (e.g. "none") even if unused
  }
}

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "sheets.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "bot" {
  repository_id = "helpdesk-faq-bot"
  location      = var.region
  format        = "DOCKER"
  description   = "Images for the helpdesk FAQ bot"
  depends_on    = [google_project_service.apis]
}

# Dedicated, least-privilege identity for the bot (no JSON key needed on Cloud Run).
resource "google_service_account" "bot" {
  account_id   = "helpdesk-faq-bot"
  display_name = "Helpdesk FAQ Bot runtime"
}

resource "google_secret_manager_secret" "bot" {
  for_each  = local.secrets
  secret_id = each.value
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# The bot may read only its own secrets.
resource "google_secret_manager_secret_iam_member" "bot_access" {
  for_each  = google_secret_manager_secret.bot
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.bot.email}"
}

resource "google_cloud_run_v2_service" "bot" {
  name     = var.service_name
  location = var.region
  # Only /metrics listens; keep it off the public internet.
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = false

  template {
    service_account = google_service_account.bot.email

    scaling {
      # Exactly one instance: Socket Mode connection + in-memory pending choices.
      min_instance_count = 1
      max_instance_count = 1
    }

    containers {
      image = local.image
      args  = ["slack"]

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle          = false # always-on CPU so the websocket isn't frozen between requests
        startup_cpu_boost = true
      }

      env {
        name  = "FAQ_SOURCE"
        value = var.google_sheet_id == "" ? "csv" : "sheets"
      }
      env {
        name  = "GOOGLE_SHEET_ID"
        value = var.google_sheet_id
      }
      env {
        name  = "LLM_BASE_URL"
        value = var.llm_base_url
      }
      env {
        name  = "LLM_MODEL"
        value = var.llm_model
      }
      env {
        name  = "METRICS_PORT"
        value = "8000"
      }

      dynamic "env" {
        for_each = local.secrets
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.bot[env.key].secret_id
              version = "latest"
            }
          }
        }
      }

      startup_probe {
        tcp_socket {
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 6
      }
    }
  }

  depends_on = [google_secret_manager_secret_iam_member.bot_access]

  # TODO: unanswered.jsonl lives on the container's ephemeral disk on Cloud Run. Use the Google
  # Sheet "Unanswered Questions" tab (set google_sheet_id) or ship logs to Cloud Logging/BigQuery.
  # TODO: Prometheus can't scrape an internal Cloud Run service from your laptop. Options:
  # Google Managed Prometheus sidecar, or push metrics via OpenTelemetry to Cloud Monitoring.
}
