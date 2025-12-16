# GitHub Actions Workflows

## Build Docker Images Workflow

This workflow automatically builds and pushes Docker images to AWS ECR when code is pushed to the `main` branch.

### Prerequisites

#### 1. Create ECR Repositories

Create three ECR repositories in your AWS account:

```bash
aws ecr create-repository --repository-name digital-brain-orchestrator --region us-east-1
aws ecr create-repository --repository-name digital-brain-frontend --region us-east-1
```

#### 2. Configure GitHub Secrets

Add the following secrets to your GitHub repository (Settings → Secrets and variables → Actions):

- `AWS_ACCESS_KEY_ID`: Your AWS access key ID
- `AWS_SECRET_ACCESS_KEY`: Your AWS secret access key
- `AWS_ACCOUNT_ID`: Your AWS account ID (12-digit number)

To create an IAM user with the necessary permissions:

```bash
# Create IAM policy with ECR permissions
cat > ecr-push-policy.json <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:GetRepositoryPolicy",
                "ecr:DescribeRepositories",
                "ecr:ListImages",
                "ecr:DescribeImages",
                "ecr:BatchGetImage",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage"
            ],
            "Resource": "*"
        }
    ]
}
EOF

# Create the policy
aws iam create-policy \
    --policy-name GitHubActionsECRPush \
    --policy-document file://ecr-push-policy.json

# Create IAM user
aws iam create-user --user-name github-actions-ecr-push

# Attach policy to user (replace <ACCOUNT_ID> with your AWS account ID)
aws iam attach-user-policy \
    --user-name github-actions-ecr-push \
    --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/GitHubActionsECRPush

# Create access key
aws iam create-access-key --user-name github-actions-ecr-push
```

#### 3. Update AWS Region (if needed)

If you're using a region other than `us-east-1`, update the `AWS_REGION` and `ECR_REGISTRY` values in `.github/workflows/build-docker-images.yml`:

```yaml
env:
  AWS_REGION: your-region  # e.g., us-west-2
  ECR_REGISTRY: ${{ secrets.AWS_ACCOUNT_ID }}.dkr.ecr.your-region.amazonaws.com
```

### What the Workflow Does

1. **Triggers on**:
   - Push to `main` branch (builds and pushes images)
   - Pull requests to `main` (builds only, doesn't push)

2. **Builds three images in parallel**:
   - `digital-brain-orchestrator`
   - `digital-brain-frontend`

3. **Tags images with**:
   - Branch name (e.g., `main`)
   - Git SHA (e.g., `main-abc1234`)
   - `latest` tag (only on main branch)

4. **Uses GitHub Actions cache** to speed up subsequent builds

### Using the Images

After the workflow runs successfully, you can pull the images:

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com

# Pull images
docker pull <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/digital-brain-orchestrator:latest
docker pull <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/digital-brain-frontend:latest
```

### Alternative: Using AWS OIDC (Recommended for Production)

For enhanced security, you can use AWS OIDC instead of long-lived access keys. This requires additional AWS setup but doesn't require storing AWS credentials in GitHub secrets.

[See AWS documentation for setting up OIDC with GitHub Actions](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services)

