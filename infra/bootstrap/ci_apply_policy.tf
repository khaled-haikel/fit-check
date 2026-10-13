locals {
  # Role changes that IAM can condition on iam:PermissionsBoundary.
  bounded_role_write_actions = [
    "iam:CreateRole",
    "iam:DeleteRole",
    "iam:DeleteRolePolicy",
    "iam:PutRolePermissionsBoundary",
    "iam:PutRolePolicy",
    "iam:UpdateAssumeRolePolicy",
    "iam:UpdateRole",
    "iam:UpdateRoleDescription",
  ]
}

# Deploy access for terraform apply on main and for the alias rollback
# workflow. The actions match the calls the AWS provider makes for the
# resources in infra/app.
data "aws_iam_policy_document" "ci_apply" {
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  # DeleteObject releases the native lock (<key>.tflock).
  statement {
    sid = "StateReadWrite"
    actions = [
      "s3:DeleteObject",
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [local.app_state_object_arns]
  }

  statement {
    sid = "LambdaManage"
    actions = [
      "lambda:AddPermission",
      "lambda:CreateAlias",
      "lambda:CreateFunction",
      "lambda:DeleteAlias",
      "lambda:DeleteFunction",
      "lambda:GetAlias",
      "lambda:GetFunction",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetFunctionConcurrency",
      "lambda:GetFunctionConfiguration",
      "lambda:GetPolicy",
      "lambda:ListAliases",
      "lambda:ListTags",
      "lambda:ListVersionsByFunction",
      "lambda:PublishVersion",
      "lambda:RemovePermission",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:UpdateAlias",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
    ]
    resources = [local.lambda_function_arns]
  }

  statement {
    sid       = "ApiGatewayCreate"
    actions   = ["apigateway:POST"]
    resources = [local.http_api_collection_arn]

    condition {
      test     = "StringLikeIfExists"
      variable = "apigateway:Request/ApiName"
      values   = ["fitcheck-*"]
    }
  }

  statement {
    sid = "ApiGatewayManage"
    actions = [
      "apigateway:DELETE",
      "apigateway:GET",
      "apigateway:PATCH",
      "apigateway:POST",
    ]
    resources = [local.http_api_arns]

    # Same guard as the plan role: refuse APIs that are not fit-check's where
    # API Gateway exposes the name or tags.
    condition {
      test     = "StringLikeIfExists"
      variable = "apigateway:Resource/ApiName"
      values   = ["fitcheck-*"]
    }

    condition {
      test     = "StringEqualsIfExists"
      variable = "aws:ResourceTag/project"
      values   = ["fit-check"]
    }
  }

  statement {
    sid = "ApiGatewayTags"
    actions = [
      "apigateway:DELETE",
      "apigateway:GET",
      "apigateway:PUT",
    ]
    resources = [local.apigateway_tag_arns]
  }

  statement {
    sid = "LogsManage"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:DeleteRetentionPolicy",
      "logs:ListTagsForResource",
      "logs:PutRetentionPolicy",
      "logs:TagLogGroup",
      "logs:TagResource",
      "logs:UntagResource",
    ]
    resources = [local.log_group_arns]
  }

  # DescribeLogGroups has no resource-level permissions.
  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  statement {
    sid = "IamReadWorkloadRoles"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:ListRoleTags",
    ]
    resources = [local.workload_role_arns]
  }

  # Every role the CI creates or changes must carry the workload boundary, so a
  # role can never hold more than the boundary allows. The CI roles have no
  # boundary, which also puts them out of reach of these actions.
  statement {
    sid       = "IamWriteBoundedWorkloadRoles"
    actions   = local.bounded_role_write_actions
    resources = [local.workload_role_arns]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.workload_boundary_arn]
    }
  }

  statement {
    sid = "IamTagWorkloadRoles"
    actions = [
      "iam:TagRole",
      "iam:UntagRole",
    ]
    resources = [local.workload_role_arns]
  }

  statement {
    sid       = "IamPassWorkloadRolesToLambda"
    actions   = ["iam:PassRole"]
    resources = [local.workload_role_arns]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  # Belt and braces: the CI can never change its own roles, strip a boundary,
  # or edit the boundary policy.
  statement {
    sid    = "DenyCiRoleChanges"
    effect = "Deny"
    actions = concat(local.bounded_role_write_actions, [
      "iam:AttachRolePolicy",
      "iam:DeleteRolePermissionsBoundary",
      "iam:DetachRolePolicy",
      "iam:PassRole",
      "iam:TagRole",
      "iam:UntagRole",
    ])
    resources = [local.ci_role_arns]
  }

  statement {
    sid       = "DenyBoundaryRemoval"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = [local.workload_role_arns]
  }

  statement {
    sid    = "DenyBoundaryPolicyChanges"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = [local.workload_boundary_arn]
  }
}
