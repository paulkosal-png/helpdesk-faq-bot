terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
  # TODO: store state remotely once this is more than a demo, e.g.
  # backend "gcs" {
  #   bucket = "YOUR-tf-state-bucket"
  #   prefix = "helpdesk-faq-bot"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
