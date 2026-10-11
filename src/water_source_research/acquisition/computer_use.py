class ComputerUseAcquirer:
    async def acquire(self, url: str):
        raise NotImplementedError(
            "Integration boundary only. Computer Use should locate/download evidence from interactive portals."
        )
