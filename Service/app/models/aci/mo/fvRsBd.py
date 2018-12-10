import logging

from . import ManagedObject
from ..utils import get_parent_dn
from ...rest import api_callback
from ...rest import api_register

# module level logging
logger = logging.getLogger(__name__)


@api_register(parent="fabric", path="mo/fvRsBd")
class fvRsBd(ManagedObject):
    META_ACCESS = ManagedObject.append_meta_access({
        "namespace": "fvRsBd",
    })

    META = ManagedObject.append_meta({
        "tDn": {},
        "parent": {
            "type": str,
            "description": "parent fvAEPg dn for this object"
        },
    })

    @staticmethod
    @api_callback("before_create")
    def before_create(data):
        """ set parent based on dn """
        data["parent"] = get_parent_dn(data["dn"])
        return data
