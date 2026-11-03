from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://trippilot:trippilot@127.0.0.1:5433/trippilot"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    session_hours: int = Field(default=24, ge=1, le=168)
    cookie_secure: bool = False
    auth_rate_limit: int = Field(default=20, ge=1, le=1000)
    provider_attempts: int = Field(default=3, ge=1, le=3)
    route_cache_seconds: int = Field(default=900, ge=1, le=86400)
    orchestrator_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:1.7b"
    ollama_timeout_seconds: float = 120.0
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.6"
    hf_token: SecretStr | None = None
    hf_model: str = "Qwen/Qwen3-32B:deepinfra"
    place_provider: str = "live"
    place_cache_hours: int = 24
    live_data_timeout_seconds: float = 30.0
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    city_search_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_fallback_url: str = "https://overpass.private.coffee/api/interpreter"
    wikimedia_api_url: str = "https://en.wikipedia.org/w/api.php"
    google_maps_api_key: SecretStr | None = None
    google_places_base_url: str = "https://places.googleapis.com/v1"
    google_routes_url: str = "https://routes.googleapis.com/directions/v2:computeRoutes"
    google_place_cache_days: int = 30
    exchange_rate_base_url: str = "https://api.frankfurter.dev"
    trippilot_user_agent: str = "TripPilotAI/0.2 (http://127.0.0.1:5173; local development)"
    amadeus_client_id: str | None = None
    amadeus_client_secret: SecretStr | None = None
    amadeus_base_url: str = "https://api.amadeus.com"
    skyscanner_api_key: SecretStr | None = None
    skyscanner_base_url: str = "https://partners.api.skyscanner.net/apiservices/v3"
    flight_cache_minutes: int = 15
    flight_location_url: str = "https://autocomplete.travelpayouts.com/places2"
    preference_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    preference_high_similarity: float = 0.52
    preference_medium_similarity: float = 0.20
    ai_solver_seconds: float = Field(default=3, ge=0.1, le=30)
    ai_candidate_limit: int = Field(default=40, ge=8, le=80)
    llm_timeout_seconds: float = Field(default=30, ge=1, le=120)
    llm_correction_attempts: int = Field(default=2, ge=1, le=3)
    llm_input_usd_per_million: float | None = Field(default=None, ge=0)
    llm_output_usd_per_million: float | None = Field(default=None, ge=0)
    embedding_backend: str = "concept_hash"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    @field_validator("overpass_url", "overpass_fallback_url")
    @classmethod
    def current_overpass_endpoint(cls, value: str) -> str:
        # The public Kumi instance moved; keep older local configuration working.
        if value.rstrip("/") in {
            "https://overpass.kumi.systems/api/interpreter",
            "http://overpass.kumi.systems/api/interpreter",
        }:
            return "https://overpass.private.coffee/api/interpreter"
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
