resource "aws_instance" "ubuntu" {
  ami           = "ami-0c02fb55956c7d316"
  instance_type = var.instance_type

  key_name = "forex-mt5-key"

  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.ubuntu_sg.id]

  iam_instance_profile = aws_iam_instance_profile.profile.name

  associate_public_ip_address = true


  user_data = <<-EOF
              #!/bin/bash
              apt update -y
              apt install -y nginx amazon-cloudwatch-agent
              systemctl start nginx
              systemctl enable nginx
              echo "Ubuntu EC2 running with Terraform" > /var/www/html/index.html
              EOF

  tags = {
    Name = "ubuntu-ec2"
  }
}

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
  }
}