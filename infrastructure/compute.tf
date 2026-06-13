# ---------------- Ubuntu EC2 (Canonical Stable AMI) ----------------

data "aws_ami" "ubuntu" {
  most_recent = true

  owners = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_instance" "ubuntu" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.instance_type

  key_name = "forex-mt5-key"

  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.ubuntu_sg.id]

  iam_instance_profile = aws_iam_instance_profile.profile.name

  associate_public_ip_address = true

  user_data = <<-EOF
              #!/bin/bash
              set -e

              apt-get update -y
              apt-get install -y nginx amazon-cloudwatch-agent

              systemctl enable nginx
              systemctl start nginx

              echo "Ubuntu EC2 running with Terraform" > /var/www/html/index.html
              EOF

  tags = {
    Name = "ubuntu-ec2"
    OS   = "ubuntu"
  }
}

# ---------------- Windows AMI (Hardened Filter) ----------------

data "aws_ami" "windows" {
  most_recent = true

  owners = ["amazon"]

  filter {
    name   = "name"
    values = ["Windows_Server-2022-English-Full-Base-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# ---------------- Windows EC2 ----------------

resource "aws_instance" "windows" {
  ami           = data.aws_ami.windows.id
  instance_type = var.instance_type

  key_name = "forex-mt5-key"

  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.windows_sg.id]

  iam_instance_profile = aws_iam_instance_profile.profile.name

  tags = {
    Name = "windows-ec2"
    OS   = "windows"
  }
}