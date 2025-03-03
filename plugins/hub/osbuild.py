"""Koji osbuild integration for Koji Hub"""
import sys
import logging
import jsonschema

import koji
from koji.context import context

sys.path.insert(0, "/usr/share/koji-hub/")
import kojihub  # pylint: disable=import-error, wrong-import-position


logger = logging.getLogger('koji.plugin.osbuild')


OSBUILD_IMAGE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-04/schema#",
    "title": "osbuildImage arguments",
    "type": "array",
    "minItems": 7,
    "items": [
        {
            "type": "string",
            "description": "Name"
        },
        {
            "type": "string",
            "description": "Version"
        },
        {
            "type": "string",
            "description": "Distribution"
        },
        {
            "oneOf": [
                {
                    "type": "string",
                    "description": "Image Type"
                },
                {
                    "type": "array",
                    "description": "Image Types (this option is deprecated)",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "string"
                    }
                }
            ]
        },
        {
            "type": "string",
            "description": "Target"
        },
        {
            "type": "array",
            "description": "Architectures",
            "minItems": 1,
            "items": {
                "type": "string"
            }
        },
        {
            "type": "object",
            "$ref": "#/definitions/options"
        }],
    "definitions": {
        "repo": {
            "title": "Repository options",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "baseurl": {
                    "type": "string"
                },
                "package_sets": {
                    "type": "array",
                    "description": "Repositories",
                    "items": {
                        "type": "string"
                    }
                }
            }
        },
        "ostree": {
            "title": "OSTree specific options",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "parent": {
                    "type": "string"
                },
                "ref": {
                    "type": "string"
                },
                "url": {
                    "type": "string"
                }
            }
        },
        "options": {
            "title": "Optional arguments",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "customizations": {
                    "type": "object",
                    "additionalProperties": True
                },
                "ostree": {
                    "type": "object",
                    "$ref": "#/definitions/ostree"
                },
                "upload_options": {
                    # this should be really 'oneOf', but the minimal required
                    # properties in AWSEC2 and GCP options overlap.
                    "anyOf": [
                        {"$ref": "#/definitions/AWSEC2UploadOptions"},
                        {"$ref": "#/definitions/AWSS3UploadOptions"},
                        {"$ref": "#/definitions/GCPUploadOptions"},
                        {"$ref": "#/definitions/AzureUploadOptions"},
                        {"$ref": "#/definitions/ContainerUploadOptions"}
                    ],
                },
                "repo": {
                    "type": "array",
                    "description": "Repositories",
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {"$ref": "#/definitions/repo"}
                        ]
                    }
                },
                "release": {
                    "type": "string",
                    "description": "Release override"
                },
                "skip_tag": {
                    "type": "boolean",
                    "description": "Omit tagging the result"
                }
            }
        },
        "AWSEC2UploadOptions": {
            "type": "object",
            "additionalProperties": False,
            "required": ["region", "share_with_accounts"],
            "properties": {
                "region": {
                    "type": "string",
                },
                "snapshot_name": {
                    "type": "string",
                },
                "share_with_accounts": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                }
            }
        },
        "AWSS3UploadOptions": {
            "type": "object",
            "additionalProperties": False,
            "required": ["region"],
            "properties": {
                "region": {
                    "type": "string"
                }
            }
        },
        "AzureUploadOptions": {
            "type": "object",
            "additionalProperties": False,
            "required": ["tenant_id", "subscription_id", "resource_group"],
            "properties": {
                "tenant_id": {
                    "type": "string"
                },
                "subscription_id": {
                    "type": "string"
                },
                "resource_group": {
                    "type": "string"
                },
                "location": {
                    "type": "string"
                },
                "image_name": {
                    "type": "string",
                }
            }
        },
        "GCPUploadOptions": {
            "type": "object",
            "additionalProperties": False,
            "required": ["region"],
            "properties": {
                "region": {
                    "type": "string"
                },
                "bucket": {
                    "type": "string"
                },
                "image_name": {
                    "type": "string",
                },
                "share_with_accounts": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    }
                }
            }
        },
        "ContainerUploadOptions": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {
                    "type": "string"
                },
                "tag": {
                    "type": "string"
                }
            }
        }
    }
}


@koji.plugin.export
def osbuildImage(name, version, distro, image_type, target, arches, opts=None, priority=None):
    """Create an image via osbuild"""
    context.session.assertPerm("image")
    args = [name, version, distro, image_type, target, arches, opts]
    task = {"channel": "image"}

    logger.info("Create osbuildImage task")

    try:
        jsonschema.validate(args, OSBUILD_IMAGE_SCHEMA)
    except jsonschema.exceptions.ValidationError as err:
        raise koji.ParameterError(str(err)) from None

    # Support array for backwards compatibility
    # This check must be done after the schema validation
    if isinstance(image_type, list):
        image_type = image_type[0]
        args = [name, version, distro, image_type, target, arches, opts]

    if priority and priority < 0 and not context.session.hasPerm('admin'):
        raise koji.ActionNotAllowed('only admins may create high-priority tasks')

    # If task_id is returned from Koji Hub we assume
    # that the task has been added to the database
    task_id = kojihub.make_task('osbuildImage', args, **task)
    if task_id:
        logger.info("osbuildImage task %i added to database", task_id)

    return task_id
