"""Serializable native design references; no alias registry or native imports at startup."""

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class DesignReference(BaseModel):
    """Native Naja DesignReference (DB, library, design), scoped to one session."""

    model_config = ConfigDict(extra="forbid")
    session_id: StrictStr = Field(min_length=1, description="Owning live Kepler session ID.")
    db_id: StrictInt = Field(ge=0, le=255, description="Native Naja database ID (8-bit).")
    library_id: StrictInt = Field(ge=0, le=65535, description="Native Naja library ID (16-bit).")
    design_id: StrictInt = Field(ge=0, le=4294967295, description="Native Naja design ID.")

    def native_key(self):
        return self.db_id, self.library_id, self.design_id


def reference_from_design(session_id, design):
    """Describe an existing object without registering or retaining a handle."""
    from kepler_formal import NativeDesign, from_najaeda

    handle = design if isinstance(design, NativeDesign) else from_najaeda(design)
    identity = handle.najaeda_design.getNLID()
    return DesignReference(session_id=session_id, db_id=identity.getDBID(),
                           library_id=identity.getLibraryID(), design_id=identity.getDesignID())
