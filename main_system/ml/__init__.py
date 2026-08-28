"""OceanTrace detection-model pipeline (Indhu / Developer 1).

Stage order:
    download -> audit -> prepare_trujillo -> train_unet -> evaluate -> export

Every stage reads its normalisation constants from config/normalisation.yaml
via `ml.config`. Nothing in this package is allowed to hardcode a dB range.
"""
