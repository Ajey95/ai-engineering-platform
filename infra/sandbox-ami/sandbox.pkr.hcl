packer {
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = "= 1.8.1"
    }
  }
}

variable "region" {
  type = string
}

variable "source_ami" {
  type = string
}

variable "build_subnet_id" {
  type = string
}

source "amazon-ebs" "guest" {
  region                      = var.region
  source_ami                  = var.source_ami
  subnet_id                   = var.build_subnet_id
  associate_public_ip_address = true
  instance_type               = "m6i.large"
  ssh_username                = "ubuntu"
  ami_name                    = "aip-sandbox-guest-{{timestamp}}"
  encrypt_boot                = true
  imds_support                = "v2.0"

  tags = {
    Application = "aip-sandbox-guest"
    ManagedBy   = "packer"
  }
}

build {
  sources = ["source.amazon-ebs.guest"]

  provisioner "file" {
    source      = "guest-runtime.tar"
    destination = "/tmp/guest-runtime.tar"
  }

  provisioner "shell" {
    script          = "provision.sh"
    execute_command = "sudo -E bash '{{ .Path }}'"
    timeout         = "45m"
  }

  post-processor "manifest" {
    output = "manifest.json"
  }
}
