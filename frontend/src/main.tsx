import {FormEvent, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import AdvancedPlanner from './AdvancedPlanner';
import './styles.css';
import './error.css';
import './orchestrator.css';
import './detail.css';

const API = import.meta.env.VITE_API_URL || '/api';
const preferenceCategories = ['museums', 'nightlife', 'food', 'nature', 'landmarks', 'entertainment'];

function reportError(error:unknown) {
  const message = error instanceof Error ? error.message : 'Something went wrong.';
  window.dispatchEvent(new CustomEvent('trip-error', {detail:message}));
}

type Trip = {id:number; destination_city:string; origin:string; start_date:string; end_date:string; traveler_count:number; total_budget:number; currency:string;pace?:string;transport_mode?:string;max_walking_per_day?:number;hotel_style?:string;flight_included?:boolean;trip_version?:number};
type Traveler = {id:number; display_name:string};
type TripDraft = {destination?:string|null; origin?:string|null; start_date?:string|null; end_date?:string|null; days?:number|null; traveler_count?:number|null; total_budget?:number|null; budget_amount?:number|null; currency?:string|null; budget_currency?:string|null; pace?:string; transport_mode?:string; walking_limit?:number; hotel_style?:string;flight_included?:boolean;preferences?:Record<string,number>;broad_preferences?:Record<string,number>;custom_preferences?:{raw_text:string}[]};
type OrchestratorStatus = {provider:string;configured:boolean;available:boolean;model:string;privacy:string;requires_api_key:boolean;detail:string};
type CitySuggestion = {id:number;name:string;display_name:string;country:string;country_code:string;region:string;latitude:number;longitude:number;timezone:string|null;population:number|null;source:string};
type FlightOffer = {total:number;per_person:number;currency:string;deep_link:string;agent?:string|string[];carriers?:string[]};
type FlightQuote = {status:'LIVE_FARE'|'TEST_FARE'|'SEARCH_REQUIRED'|'NOT_INCLUDED';price_type?:string;provider:string|null;currency:string;total:number|null;per_person:number|null;allocated_budget:number;search_url:string;search_provider?:string;origin_code?:string|null;destination_code?:string|null;departure_date?:string;return_date?:string;traveler_count?:number;source_url:string|null;booking_url?:string|null;evidence_url?:string|null;booking_is_provider_offer?:boolean;checked_at:string;fetched_at?:string;cache_age_seconds?:number;cached?:boolean;message:string;carriers?:string[];offers?:FlightOffer[];price_confirmed?:boolean;search_complete?:boolean};
type BudgetGuidance = {requested_budget:number;full_itinerary_minimum:number;full_itinerary_expected:number;activities_removed:number;flexible_costs_reduced:boolean;flight_pricing_status:string;live_airfare_included:boolean;flight_caveat?:string|null};
type VenueTrust = {rating?:number|null;review_count?:number;rating_source?:string|null;phone_number?:string|null;phone_source_url?:string|null};
type TransportOption = {mode:string;cost_per_person:number;group_cost:number;minimum_group_cost:number;maximum_group_cost:number;duration_minutes:number;source_quality:string;source_reference:string;calculation:string;route_source:string;vehicles_required:number;fx_source?:string|null};
type TransportDetails = {from:string;to:string;currency:string;traveler_count:number;distance_km:number;selected_mode:string;selected_group_cost:number;selected_cost_per_person:number;duration_minutes:number;selection_reason:string;directions_url:string;source_quality:string;options:TransportOption[]};
type BudgetMinimumEstimate = {status:string;destination:string;origin?:string|null;currency:string;traveler_count:number;days:number;nights:number;rooms:number;minimum_budget:number;minimum_per_person:number;components:{category:string;amount:number;explanation:string}[];assumptions:string[];flight_included:boolean;flight_is_live:boolean;fx_source:string;message:string};
type FeaturedPlace = VenueTrust & {name:string;category:string;neighborhood:string;image_url:string|null;image_source:string|null;cost:number|null;group_cost:number|null;traveler_count:number;published_cost?:number|null;published_currency?:string|null;cost_status:string;cost_currency:string;ticket_url:string|null;official_url:string|null;cost_source?:string|null;maps_url:string};
type Plan = {
  id:number; plan_type:string; title:string; estimated_total_cost:number; estimated_per_person_cost:number;
  mean_group_satisfaction:number; minimum_traveler_satisfaction:number; fairness_score:number;
  travel_minutes:number; walking_intensity:string; activity_count:number; status:string; vote_count:number; favorite_count:number;
  summary:{engine?:string;strategy?:string;tagline:string; pace:string; neighborhoods:string[]; day_summaries:string[]; category_budget:Record<string,number>; activity_cost:number; main_tradeoffs:string;currency?:string;traveler_count?:number;pricing_complete?:boolean;featured_places?:FeaturedPlace[];flight_quote?:FlightQuote|null;budget_status?:string;budget_delta?:number;over_by?:number;under_by?:number;cost_breakdown?:Record<string,{minimum:number;expected:number;maximum:number;price_types:string[]}>;cost_reasons?:string[];price_freshness?:string;budget_guidance?:BudgetGuidance};
};
type ComparedPlan = {id:number;title:string;strategy?:string;display_name:string;decision_tip:string;total:number};
type PlanComparison = {currency:string;total_cost_delta:number;verdict:{cheaper:'plan_a'|'plan_b'|'same';savings:number};plan_a:ComparedPlan;plan_b:ComparedPlan;category_deltas:Record<string,number>;category_costs:Record<string,{label:string;plan_a:number;plan_b:number;delta:number}>;shared_places:string[];only_in_plan_a:string[];only_in_plan_b:string[];daily_differences:{day:number;only_in_a:string[];only_in_b:string[];travel_minutes_delta:number;activity_cost_delta:number}[];reasons:string[]};
type ChatGeneration = {trip:Trip;travelers:Traveler[];plans:Plan[];flight_quote?:FlightQuote|null};
type ContextSource = {source_type:string;source_name:string;source_url:string;claim_types:string[];retrieved_at:string};
type PlaceContext = {place_id:number;short_description:string;why_visit:string[];known_for:string[];historical_significance?:string|null;cultural_significance?:string|null;architectural_significance?:string|null;famous_people:string[];notable_events:string[];movie_tv_connections:string[];pop_culture_connections:string[];local_tips:string[];interesting_facts:string[];best_experience?:string|null;recommended_visit_focus:string[];source_records:ContextSource[];last_verified_at:string};
type DetailItem = VenueTrust & {item_id:string;place_id:number;day_number:number;photo_url_or_reference:string;photo_source:string;photo_source_url?:string|null;photo_attribution?:string;name:string;category:string;subcategory:string;city:string;borough_or_district:string;neighborhood:string;address:string;start_time:string;end_time:string;start_minute:number;end_minute:number;duration_minutes:number;duration_min:number;duration_expected:number;duration_max:number;best_time_of_day:string;cost_min:number;cost_expected:number;cost_max:number;group_cost_min:number;group_cost_expected:number;group_cost_max:number;traveler_count:number;published_cost:number;published_currency:string;cost_status:string;cost_source:string|null;cost_currency:string;group_match:number;individual_matches:{traveler_id:number;traveler_name:string;score:number}[];travel_minutes_from_previous:number;break_minutes_after_previous:number;transport_mode:string;transport_details?:TransportDetails|null;opening_feasible:boolean;opening_status:string;opening_hours?:string|null;why_selected:string;maps_url:string;directions_url?:string|null;official_url:string|null;ticket_url:string|null;reservation_url:string|null;is_locked:boolean;context?:PlaceContext|null};
type MealItem = VenueTrust & {id:number;item_id:string;day_number:number;meal_type:'breakfast'|'lunch'|'dinner';category:'food';subcategory:string;restaurant_id:number;restaurant_name:string;name:string;cuisine:string[];neighborhood:string;borough_or_district:string;address:string;photo_url_or_reference:string;photo_source:string;photo_source_url?:string|null;photo_attribution?:string;start_time:string;end_time:string;start_minute:number;end_minute:number;duration_minutes:number;traveler_count:number;min_cost_per_person:number;expected_cost_per_person:number;max_cost_per_person:number;expected_total:number;reason_selected:string;why_selected:string;assumptions:string[];source_quality:string;travel_minutes_from_previous:number;break_minutes_after_previous:number;transport_mode:string;transport_details?:TransportDetails|null;maps_url:string;directions_url?:string|null;menu_url?:string|null;official_url?:string|null;reservation_url?:string|null;context?:PlaceContext|null};
type ExpenseLine = {id:number;category:string;subcategory:string;day_number:number|null;itinerary_item_id?:string|null;title:string;description:string;quantity:number;unit:string;unit_cost_min:number;unit_cost_expected:number;unit_cost_max:number;total_min:number;total_expected:number;total_max:number;currency:string;calculation_expression:string;assumptions:string[];source_type:string;source_reference?:string|null;source_quality:string;metadata?:Record<string,unknown>};
type ExpenseBreakdown = {plan_id:number;category:string|null;day_number:number|null;currency:string;minimum:number;expected:number;maximum:number;days:{day:string;expected:number;items:ExpenseLine[]}[]};
type ExpenseDashboard = {plan_id:number;currency:string;expected_total:number;categories:{category:string;minimum:number;expected:number;maximum:number;day_totals:Record<string,number>;line_count:number}[];reconciliation:{valid:boolean;checks:Record<string,boolean>;expected_total:number;category_totals:Record<string,number>;day_totals:Record<string,number>}};
type PlanDetail = {plan_id:number;stage:string;title:string;trip:{destination:string;start_date:string;end_date:string;traveler_count:number;currency:string};group_satisfaction:number;flight_quote?:FlightQuote|null;expenses:ExpenseDashboard;budget:{minimum:number;expected:number;safe:number;target:number;status:string;expected_per_person:number;activity_categories:Record<string,number>;pricing_complete?:boolean;reconciliation?:ExpenseDashboard['reconciliation']};days:{day_number:number;date:string;region_summary:string;hotel_departure_time:string;finish_time:string;hotel_departure_minute:number;finish_minute:number;cost_expected:number;expense_total_expected:number;travel_minutes:number;walking_intensity:string;items:DetailItem[];meals:MealItem[];timeline:(DetailItem|MealItem)[]}[];validation:{valid:boolean;checks:Record<string,boolean|string>};data_provenance:Record<string,string>};

function money(currency:string, amount:number|null|undefined) {
  if (amount == null) return 'Price unavailable';
  try { return new Intl.NumberFormat(undefined,{style:'currency',currency,maximumFractionDigits:2}).format(amount); }
  catch { return `${currency} ${amount.toFixed(2)}`; }
}

async function request<T>(path:string, options?:RequestInit):Promise<T> {
  const response = await fetch(`${API}${path}`, {...options, credentials:'include', headers:{'Content-Type':'application/json','X-Requested-With':'TripPilot',...options?.headers}});
  if (!response.ok) {
    const body = await response.json().catch(() => ({detail:'Something went wrong'}));
    if (response.status === 401 && !path.includes('/auth/')) window.dispatchEvent(new Event('session-expired'));
    throw new Error(body.error?.message || (typeof body.detail === 'string' ? body.detail : body.detail?.message) || 'Please check the form and try again.');
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

function ChatText({content}:{content:string}) {
  const parts = content.split(/(\[[^\]]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s]+)/g);
  return <>{parts.map((part,index)=>{
    const markdown = part.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
    const rawUrl = part.match(/^https?:\/\/[^\s]+$/);
    if (markdown) return <a key={index} href={markdown[2]} target="_blank" rel="noreferrer">{markdown[1]}</a>;
    if (rawUrl) return <a key={index} href={part} target="_blank" rel="noreferrer">Open source</a>;
    return part;
  })}</>;
}

function useCitySuggestions(query:string, enabled=true) {
  const [suggestions,setSuggestions] = useState<CitySuggestion[]>([]);
  const [searching,setSearching] = useState(false);
  useEffect(()=>{
    const normalized = query.trim();
    if (!enabled || normalized.length < 2) { setSuggestions([]); setSearching(false); return; }
    const controller = new AbortController();
    const timer = window.setTimeout(async()=>{
      setSearching(true);
      try { setSuggestions(await request<CitySuggestion[]>(`/locations/search?q=${encodeURIComponent(normalized)}&limit=6`, {signal:controller.signal})); }
      catch(error) { if (!(error instanceof DOMException && error.name === 'AbortError')) setSuggestions([]); }
      finally { if (!controller.signal.aborted) setSearching(false); }
    }, 350);
    return ()=>{ window.clearTimeout(timer); controller.abort(); };
  },[query,enabled]);
  return {suggestions,searching,clear:()=>setSuggestions([])};
}

function CitySuggestionList({items,onSelect}:{items:CitySuggestion[];onSelect:(city:CitySuggestion)=>void}) {
  if (!items.length) return null;
  return <div className="city-suggestions" role="listbox">{items.map(city=><button key={`${city.id}-${city.latitude}`} type="button" onMouseDown={event=>event.preventDefault()} onClick={()=>onSelect(city)}><b>{city.name}</b><span>{[city.region,city.country].filter(Boolean).join(', ')}</span></button>)}<small>Live city results · Open-Meteo</small></div>;
}

function CityInput({label,name,initial,onValueChange}:{label:string;name:string;initial:string;onValueChange?:(value:string)=>void}) {
  const [value,setValue] = useState(initial);
  const [focused,setFocused] = useState(false);
  const lookup = useCitySuggestions(value,focused);
  return <label className="city-lookup">{label}<input name={name} value={value} autoComplete="off" onFocus={()=>setFocused(true)} onBlur={()=>window.setTimeout(()=>setFocused(false),120)} onChange={event=>{setValue(event.target.value);onValueChange?.(event.target.value);}} required/>{lookup.searching&&<small className="city-searching">Searching cities online…</small>}<CitySuggestionList items={focused?lookup.suggestions:[]} onSelect={city=>{setValue(city.name);onValueChange?.(city.name);lookup.clear();setFocused(false);}}/></label>;
}

function BudgetMinimumHint({values,enteredBudget,compact=false}:{values:{destination?:string|null;origin?:string|null;start_date?:string|null;end_date?:string|null;traveler_count?:number|null;currency?:string|null;hotel_style?:string;flight_included?:boolean};enteredBudget?:number|null;compact?:boolean}) {
  const [estimate,setEstimate] = useState<BudgetMinimumEstimate|null>(null); const [loading,setLoading] = useState(false);
  useEffect(()=>{
    const destination=values.destination?.trim(); const travelers=Number(values.traveler_count||0); if(!destination||destination.length<2||travelers<1){setEstimate(null);return;}
    const controller=new AbortController(); const timer=window.setTimeout(async()=>{setLoading(true);try{setEstimate(await request<BudgetMinimumEstimate>('/budget/minimum-estimate',{method:'POST',signal:controller.signal,body:JSON.stringify({destination,origin:values.origin||null,start_date:values.start_date||null,end_date:values.end_date||null,traveler_count:travelers,currency:values.currency||'USD',hotel_style:values.hotel_style||'budget',flight_included:values.flight_included!==false})}));}catch(error){if(!(error instanceof DOMException&&error.name==='AbortError'))setEstimate(null);}finally{if(!controller.signal.aborted)setLoading(false);}},420);
    return()=>{window.clearTimeout(timer);controller.abort();};
  },[values.destination,values.origin,values.start_date,values.end_date,values.traveler_count,values.currency,values.hotel_style,values.flight_included]);
  if(!estimate)return loading?<div className={`minimum-budget-hint ${compact?'compact':''}`}><span className="hint-spinner"/> Estimating a practical minimum…</div>:null;
  const gap=enteredBudget&&enteredBudget<estimate.minimum_budget?estimate.minimum_budget-enteredBudget:0;
  return <details className={`minimum-budget-hint ${compact?'compact':''}`} open={!compact}><summary><span><small>Suggested minimum for this trip</small><b>{money(estimate.currency,estimate.minimum_budget)} for {estimate.traveler_count} traveler{estimate.traveler_count===1?'':'s'}</b></span><em>{gap>0?`Add at least ${money(estimate.currency,gap)}`:'Use this amount as a starting point'}</em></summary><div className="minimum-components">{estimate.components.map(row=><span key={row.category}><b>{row.category}</b>{money(estimate.currency,row.amount)}<small>{row.explanation}</small></span>)}</div><p>This is a planning estimate, not a live quote. {estimate.assumptions.slice(0,3).join(' ')}</p></details>;
}

function NaturalTripInput({onState,onGenerated}:{onState:(state:TripDraft)=>void;onGenerated:(result:ChatGeneration)=>void}) {
  const [sessionId,setSessionId] = useState<number|null>(null); const [message,setMessage] = useState(''); const [conversation,setConversation] = useState<{role:string;content:string}[]>([]); const [mode,setMode] = useState(''); const [working,setWorking] = useState(false); const [generating,setGenerating] = useState(false); const [ready,setReady] = useState(false); const [tripState,setTripState] = useState<TripDraft>({}); const [runtime,setRuntime] = useState<OrchestratorStatus|null>(null); const inputRef = useRef<HTMLTextAreaElement>(null); const chatLogRef = useRef<HTMLDivElement>(null);
  useEffect(()=>{ request<OrchestratorStatus>('/orchestrator/status').then(setRuntime).catch(()=>setRuntime(null)); },[]);
  useEffect(()=>{ if (!working) inputRef.current?.focus(); },[working,conversation.length]);
  useEffect(()=>{ chatLogRef.current?.scrollTo({top:chatLogRef.current.scrollHeight,behavior:'smooth'}); },[conversation]);
  const send = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault(); if (working || !message.trim()) return; setWorking(true);
    try {
      let activeSession = sessionId;
      if (!activeSession) { const created = await request<{id:number}>('/orchestrator/sessions', {method:'POST'}); activeSession = created.id; setSessionId(created.id); }
      const response = await request<{assistant_message:string; parser_mode:string; ready:boolean; state:TripDraft}>(`/orchestrator/sessions/${activeSession}/messages`, {method:'POST', body:JSON.stringify({message})});
      setConversation(current=>[...current,{role:'user',content:message},{role:'assistant',content:response.assistant_message}]); setMode(response.parser_mode); setReady(response.ready); setTripState(response.state); onState(response.state); setMessage('');
    } catch(error) { reportError(error); } finally { setWorking(false); }
  };
  const generate = async () => { if(!sessionId)return; setGenerating(true); try{onGenerated(await request<ChatGeneration>(`/orchestrator/sessions/${sessionId}/generate-plans`,{method:'POST'}));}catch(error){reportError(error);}finally{setGenerating(false);} };
  const startOver = () => { setSessionId(null); setConversation([]); setMode(''); setMessage(''); setReady(false); setTripState({}); onState({}); };
  const providerLabel = mode === 'ollama_local' ? `Local ${runtime?.model || 'Qwen'} · no key` : mode === 'conversation' ? `Local ${runtime?.model || 'Qwen'} answer · no key` : mode === 'contextual_followup' ? 'Instant local follow-up' : mode.startsWith('openai') ? 'OpenAI structured output' : mode ? 'Deterministic safety parser' : runtime?.available ? `Local ${runtime.model} ready · no key` : runtime ? 'Local model starting · safety parser ready' : 'Checking local model…';
  const cityOnlyMessage = !ready && (!tripState.destination || !tripState.origin) && /^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ .'-]{1,49}$/.test(message.trim()) && message.trim().split(/\s+/).length <= 4 && !/\b(?:i|we|want|go|travel|plan|budget|people|days?|all|them|museum|museums|food|park|parks|club|clubs|pub|pubs)\b/i.test(message);
  const chatCityLookup = useCitySuggestions(message,cityOnlyMessage&&!working);
  const savedInterests = Array.from(new Set([...Object.keys(tripState.preferences||{}),...Object.keys(tripState.broad_preferences||{}),...(tripState.custom_preferences||[]).map(item=>item.raw_text)]));
  return <section className="nl-panel"><div className="nl-copy"><div className="eyebrow">Start naturally</div><h2>Tell us the trip in your own words.</h2><p>TripPilot remembers every detail you already gave, asks only for missing basics, answers your questions, and then builds four suggested itineraries directly.</p><div className="trust-row"><span>Runs locally</span><span>No API key</span><span>Schema validated</span><span>No invented prices</span></div></div><div className="chat-card"><div className="chat-title"><b>TripPilot orchestrator</b><small className={runtime?.available ? 'runtime-ready' : 'runtime-safe'}><i/>{providerLabel}</small></div><div className="chat-log" ref={chatLogRef}>{conversation.length ? conversation.map((item,index)=><div key={index} className={`bubble ${item.role}`}><ChatText content={item.content}/></div>) : <div className="chat-empty"><b>Where would you like to go?</b><span>Answer with a city, describe the whole trip, or ask me a question.</span></div>}</div>{conversation.length>0&&<button type="button" className="reset-chat" onClick={startOver}>Start over</button>}{ready&&<div className="chat-ready"><div className="ready-check">✓</div><div><b>Your trip brief is complete</b><p>{tripState.destination} · {tripState.traveler_count} people · {tripState.start_date} → {tripState.end_date}</p><div className="ready-interests">{savedInterests.map(item=><span key={item}>{item}</span>)}</div><small>No names needed—we'll use {tripState.traveler_count} anonymous traveler profiles.</small></div><button type="button" onClick={generate} disabled={generating}>{generating?'Generating four plans…':'Generate four plans →'}</button></div>}{ready&&<BudgetMinimumHint values={{...tripState,destination:tripState.destination,origin:tripState.origin,traveler_count:tripState.traveler_count,currency:tripState.currency||tripState.budget_currency,flight_included:tripState.flight_included!==false}} enteredBudget={tripState.total_budget||tripState.budget_amount} compact/>}<form className={ready?'chat-followup-form':''} onSubmit={send}><div className="chat-city-lookup"><textarea ref={inputRef} autoFocus value={message} onChange={event=>setMessage(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();event.currentTarget.form?.requestSubmit();}}} rows={ready?2:4} aria-label="Chat with TripPilot" placeholder={ready?'Ask a question or update any trip detail…':'Type your answer or ask a question…'}/>{chatCityLookup.searching&&<small className="city-searching">Searching cities online…</small>}<CitySuggestionList items={chatCityLookup.suggestions} onSelect={city=>{setMessage(city.name);chatCityLookup.clear();inputRef.current?.focus();}}/></div><small className="input-hint">Enter to send · Shift+Enter for a new line</small><button disabled={working}>{working ? 'Thinking locally…' : ready ? 'Ask or update TripPilot →' : 'Send to TripPilot →'}</button></form></div></section>;
}

function TripForm({onCreated,draft}:{onCreated:(trip:Trip)=>void;draft:TripDraft}) {
  const [hintValues,setHintValues] = useState({destination:draft.destination||'New York City',origin:draft.origin||'Chicago',start_date:draft.start_date||'',end_date:draft.end_date||'',traveler_count:draft.traveler_count||4,currency:draft.currency||draft.budget_currency||'USD',hotel_style:draft.hotel_style||'midrange',flight_included:draft.flight_included!==false});
  const [enteredBudget,setEnteredBudget] = useState(Number(draft.total_budget||draft.budget_amount||3500));
  const submit = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try {
      onCreated(await request<Trip>('/trips', {method:'POST', body:JSON.stringify({...data, traveler_count:Number(data.traveler_count), total_budget:Number(data.total_budget), max_walking_per_day:Number(data.max_walking_per_day),flight_included:data.flight_included==='on'})}));
    } catch (error) { reportError(error); }
  };
  return <form key={JSON.stringify(draft)} onSubmit={submit} className="panel">
    <div className="eyebrow">01 · Trip basics</div><h2>Where are you headed?</h2>
    <div className="grid">
      <CityInput label="Destination" name="destination_city" initial={draft.destination || 'New York City'} onValueChange={destination=>setHintValues(current=>({...current,destination}))}/>
      <CityInput label="Origin" name="origin" initial={draft.origin || 'Chicago'} onValueChange={origin=>setHintValues(current=>({...current,origin}))}/>
      <label>Start date<input type="date" name="start_date" defaultValue={draft.start_date || ''} onChange={event=>setHintValues(current=>({...current,start_date:event.target.value}))} required/></label>
      <label>End date<input type="date" name="end_date" defaultValue={draft.end_date || ''} onChange={event=>setHintValues(current=>({...current,end_date:event.target.value}))} required/></label>
      <label>Travelers<input type="number" name="traveler_count" min="1" max="20" defaultValue={draft.traveler_count || 4} onChange={event=>setHintValues(current=>({...current,traveler_count:Number(event.target.value)}))}/></label>
      <div className="budget-field"><label>Total budget<input type="number" name="total_budget" min="1" defaultValue={draft.total_budget || 3500} onChange={event=>setEnteredBudget(Number(event.target.value))}/></label><BudgetMinimumHint values={hintValues} enteredBudget={enteredBudget} compact/></div>
      <label>Currency<select name="currency" defaultValue={draft.currency || 'USD'} onChange={event=>setHintValues(current=>({...current,currency:event.target.value}))}><option>USD</option><option>INR</option><option>GBP</option><option>EUR</option><option>CAD</option><option>AUD</option><option>JPY</option></select></label>
      <label>Pace<select name="pace" defaultValue={draft.pace || 'balanced'}><option value="balanced">Balanced</option><option value="relaxed">Relaxed</option><option value="packed">Packed</option></select></label>
      <label>Transport<select name="transport_mode" defaultValue={draft.transport_mode || 'mixed'}><option value="mixed">Cheapest of cab / transit</option><option value="walking">Walking</option><option value="public transit">Public transit</option><option value="cab">Cab / taxi</option></select></label>
      <label>Daily walking limit<input type="number" name="max_walking_per_day" defaultValue={draft.walking_limit || 120}/><small>minutes</small></label>
      <label>Hotel style<select name="hotel_style" defaultValue={draft.hotel_style || 'midrange'} onChange={event=>setHintValues(current=>({...current,hotel_style:event.target.value}))}><option value="budget">Budget</option><option value="midrange">Midrange</option><option value="premium">Premium</option><option value="luxury">Luxury</option></select></label>
      <label className="flight-toggle"><input type="checkbox" name="flight_included" defaultChecked={draft.flight_included!==false} onChange={event=>setHintValues(current=>({...current,flight_included:event.target.checked}))}/> Include flights</label>
    </div><button type="submit">Create group trip <span>→</span></button>
  </form>;
}

function TravelerForm({trip, travelers, onAdded}:{trip:Trip; travelers:Traveler[]; onAdded:(traveler:Traveler)=>void}) {
  const submit = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    try {
      const traveler = await request<Traveler>(`/trips/${trip.id}/travelers`, {method:'POST', body:JSON.stringify({display_name:data.get('display_name')})});
      const preferences = preferenceCategories.map(category => ({category, subcategory:'general', score:Number(data.get(category))}));
      await request(`/travelers/${traveler.id}/preferences`, {method:'PUT', body:JSON.stringify({preferences})});
      onAdded(traveler); form.reset();
    } catch (error) { reportError(error); }
  };
  const complete = travelers.length >= trip.traveler_count;
  return <section className="panel">
    <div className="eyebrow">02 · Your group</div><div className="section-title"><div><h2>Whose trip is this?</h2><p>{travelers.length} of {trip.traveler_count} travelers added</p></div><div className="progress"><i style={{width:`${travelers.length / trip.traveler_count * 100}%`}}/></div></div>
    <div className="traveler-list">{travelers.map((traveler,index)=><span key={traveler.id}>{index + 1}. {traveler.display_name}</span>)}</div>
    {!complete && <form onSubmit={submit} className="traveler-form"><label>Name<input name="display_name" placeholder="Traveler name" required/></label><div className="preference-grid">{preferenceCategories.map(category=><label key={category}>{category}<input type="range" name={category} min="0" max="10" defaultValue="5"/></label>)}</div><button type="submit">Add traveler</button></form>}
  </section>;
}

function BudgetForm({trip, onSaved}:{trip:Trip; onSaved:()=>void}) {
  const [saved,setSaved] = useState(false);
  const shares:Record<string,number> = {flight:.20, hotel:.35, food:.20, nightlife:.08, attraction:.12, buffer:.05};
  const submit = async (event:FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const data = new FormData(event.currentTarget); const categories:Record<string,number> = {};
    ['flight','hotel','food','nightlife','attraction','buffer'].forEach(key => categories[key] = Number(data.get(key)) || 0);
    try {
      await request(`/trips/${trip.id}/budget`, {method:'PUT', body:JSON.stringify({mode:'fixed', categories})}); setSaved(true); onSaved();
    } catch (error) { reportError(error); }
  };
  return <section className="panel compact"><div className="eyebrow">03 · Budget targets</div><h2>Shape the spend</h2><form onSubmit={submit}><div className="budget-grid">{['flight','hotel','food','nightlife','attraction','buffer'].map(key=><label key={key}>{key}<span><b>{trip.currency}</b><input type="number" min="0" name={key} defaultValue={Math.round(trip.total_budget * shares[key])}/></span></label>)}</div><button type="submit">{saved ? 'Budget saved ✓' : 'Save budget'}</button></form></section>;
}

function Compare({plans,currency}:{plans:Plan[];currency:string}) {
  const [result,setResult] = useState<PlanComparison|null>(null);
  useEffect(()=>{if(plans.length!==2){setResult(null);return;}request<PlanComparison>('/plans/compare',{method:'POST',body:JSON.stringify({plan_a_id:plans[0].id,plan_b_id:plans[1].id})}).then(setResult).catch(reportError);},[plans.map(plan=>plan.id).join(',')]);
  if (plans.length !== 2) return null;
  if (!result) return <div className="compare-loading">Calculating exact differences…</div>;
  const shownCurrency = result.currency || currency;
  const planA = plans.find(plan=>plan.id===result.plan_a.id) || plans[0];
  const planB = plans.find(plan=>plan.id===result.plan_b.id) || plans[1];
  const verdict = result.verdict.cheaper === 'same'
    ? 'Both plans cost about the same'
    : `${result.verdict.cheaper === 'plan_a' ? result.plan_a.display_name : result.plan_b.display_name} saves ${money(shownCurrency,result.verdict.savings)} overall`;
  const verdictDetail = result.verdict.cheaper === 'same'
    ? 'Use the pace and places below to choose the better fit for your group.'
    : `That is the estimated group saving compared with ${result.verdict.cheaper === 'plan_a' ? result.plan_b.display_name : result.plan_a.display_name}.`;
  const categoryHelp:Record<string,string> = {activities:'Entry fees and tours',attraction:'Entry fees and tours',buffer:'Money kept aside for surprises',flight:'Round-trip airfare',food:'Meals and drinks',hotel:'Your stay',nightlife:'Evening activities',shopping:'Optional spending',transport:'Local trains, buses, and rides'};
  const categoryOrder = ['flight','hotel','activities','attraction','food','transport','nightlife','shopping','buffer'];
  const categories = Object.entries(result.category_costs).sort(([left],[right])=>{
    const aIndex=categoryOrder.indexOf(left), bIndex=categoryOrder.indexOf(right);
    return (aIndex<0?99:aIndex)-(bIndex<0?99:bIndex);
  });
  const difference = (delta:number) => delta === 0
    ? {text:'Same price',kind:'same'}
    : delta < 0
      ? {text:`Plan B saves ${money(shownCurrency,Math.abs(delta))}`,kind:'saving'}
      : {text:`Plan B costs ${money(shownCurrency,delta)} more`,kind:'cost'};
  const planFacts = (plan:Plan, compared:ComparedPlan) => `${compared.decision_tip} It schedules ${plan.activity_count} ${plan.activity_count===1?'place':'places'}, with ${plan.walking_intensity.toLowerCase()} walking and about ${plan.travel_minutes} minutes of travel between stops.`;
  const placeList = (items:string[]) => items.length ? <ul>{items.slice(0,6).map(item=><li key={item}>{item}</li>)}{items.length>6&&<li>Plus {items.length-6} more</li>}</ul> : <p className="no-extra-places">No exclusive stops—this plan shares the same main places.</p>;
  return <section className="tourist-compare">
    <div className="comparison-verdict"><div><span className="eyebrow">The simple answer</span><h3>{verdict}</h3><p>{verdictDetail}</p></div><strong>{result.verdict.cheaper==='same'?'Similar total':`${money(shownCurrency,result.verdict.savings)} difference`}</strong></div>
    <div className="compare-plan-heads">
      <article><span className="plan-letter">Plan A</span><h3>{result.plan_a.display_name}</h3><p>{result.plan_a.title}</p><strong>{money(shownCurrency,result.plan_a.total)} total</strong></article>
      <article><span className="plan-letter">Plan B</span><h3>{result.plan_b.display_name}</h3><p>{result.plan_b.title}</p><strong>{money(shownCurrency,result.plan_b.total)} total</strong></article>
    </div>
    <div className="tourist-cost-table"><div className="cost-table-head"><b>What you pay for</b><b>Plan A</b><b>Plan B</b><b>Plain-English difference</b></div>{categories.map(([category,row])=>{const delta=difference(row.delta);return <div className="friendly-cost-row" key={category}><span><b>{row.label}</b><small>{categoryHelp[category]||'Estimated group cost'}</small></span><strong>{money(shownCurrency,row.plan_a)}</strong><strong>{money(shownCurrency,row.plan_b)}</strong><em className={delta.kind}>{delta.text}</em></div>;})}</div>
    <div className="choose-plan-grid"><article><span className="plan-letter">Choose Plan A if…</span><h3>{result.plan_a.display_name}</h3><p>{planFacts(planA,result.plan_a)}</p></article><article><span className="plan-letter">Choose Plan B if…</span><h3>{result.plan_b.display_name}</h3><p>{planFacts(planB,result.plan_b)}</p></article></div>
    <div className="place-comparison"><h3>What changes in the itinerary?</h3><p>These are the stops that make each option different.</p><div><article><b>Only in Plan A</b>{placeList(result.only_in_plan_a)}</article><article><b>Only in Plan B</b>{placeList(result.only_in_plan_b)}</article></div>{result.shared_places.length>0&&<details><summary>{result.shared_places.length} places are included in both plans</summary><p>{result.shared_places.join(' · ')}</p></details>}</div>
  </section>;
}

function priceLabel(place:{cost:number|null;cost_status:string;cost_currency:string;published_cost?:number|null;published_currency?:string|null}) {
  if (place.cost_status === 'FREE') return 'Free entry';
  if (place.cost_status === 'UNKNOWN') return 'Price not published';
  if (place.cost_status === 'PUBLISHED_UNCONVERTED') return `Published ${money(place.published_currency||place.cost_currency,place.published_cost)}`;
  const prefix = ['PUBLISHED','LIVE'].includes(place.cost_status) ? '' : 'About ';
  return `${prefix}${money(place.cost_currency,place.cost)} per person`;
}

function FlightQuoteCard({quote:initial,trip}:{quote?:FlightQuote|null;trip?:Trip}) {
  const [quote,setQuote] = useState(initial); const [refreshing,setRefreshing] = useState(false);
  useEffect(()=>setQuote(initial),[initial]);
  if (!quote) return null;
  const live = quote.status === 'LIVE_FARE';
  const testFare = quote.status === 'TEST_FARE';
  const travelers = quote.traveler_count || 1;
  const route = quote.origin_code&&quote.destination_code ? `${quote.origin_code} → ${quote.destination_code}` : 'Flight route';
  const refresh = async()=>{if(!trip)return;setRefreshing(true);try{setQuote(await request<FlightQuote>('/flights/search',{method:'POST',body:JSON.stringify({origin:trip.origin,destination:trip.destination_city,departure_date:trip.start_date,return_date:trip.end_date,adults:trip.traveler_count,cabin:'economy',currency:trip.currency,refresh:true})}));}catch(error){reportError(error);}finally{setRefreshing(false);}};
  const flightLink = quote.booking_url || quote.source_url || quote.search_url;
  return <section className={`flight-quote ${live?'live':''}`}><div><div className="eyebrow">Flight cost · {live&&quote.price_confirmed?'API-confirmed live':'live provider required'}</div><h3>{quote.status === 'NOT_INCLUDED' ? 'Flights excluded from this budget' : live ? `${money(quote.currency,quote.total)} for ${travelers}` : testFare ? `${money(quote.currency,quote.total)} test fare — not live` : 'Live fare unavailable'}</h3><p className="flight-route"><b>{route}</b> · {quote.departure_date} → {quote.return_date} · <b>{travelers} {travelers===1?'adult':'adults'}</b></p><p>{live&&`${money(quote.currency,quote.per_person)} per person · `}{quote.message}</p>{!live&&quote.status!=='NOT_INCLUDED'&&<small><b>{money(quote.currency,quote.allocated_budget)} is a budget reserve, not a flight rate.</b> Numeric airfare stays hidden until a production flight API returns it.</small>}{quote.fetched_at&&<small className="freshness">Checked {new Date(quote.fetched_at).toLocaleString()}{quote.cached?` · cached ${quote.cache_age_seconds}s`:''}</small>}{Boolean(quote.offers?.length)&&<div className="flight-offers">{quote.offers!.slice(0,3).map((offer,index)=><a key={`${offer.deep_link}-${index}`} href={offer.deep_link} target="_blank" rel="noreferrer">{money(offer.currency,offer.total)} group · {money(offer.currency,offer.per_person)} each ↗</a>)}</div>}</div><div className="flight-actions"><a href={flightLink} target="_blank" rel="noreferrer">{live&&quote.booking_is_provider_offer?'Open exact priced offer ↗':live?'Recheck this exact route ↗':`Search ${route} for ${travelers} adults ↗`}</a>{trip&&quote.status!=='NOT_INCLUDED'&&<button className="ghost" onClick={refresh} disabled={refreshing}>{refreshing?'Refreshing…':'Refresh fare'}</button>}</div></section>;
}

function BudgetGuidanceCard({guidance,currency}:{guidance?:BudgetGuidance;currency:string}) {
  if (!guidance) return null;
  const constrained = guidance.full_itinerary_expected > guidance.requested_budget || guidance.activities_removed > 0 || guidance.flexible_costs_reduced;
  if (!constrained) return null;
  return <section className="budget-guidance"><b>Your budget is enforced</b><p>A full Smart Spend version is estimated at {money(currency,guidance.full_itinerary_expected)}, with a lower-range minimum of {money(currency,guidance.full_itinerary_minimum)}. TripPilot kept the generated plan at or below {money(currency,guidance.requested_budget)} by reducing flexible costs{guidance.activities_removed?` and removing ${guidance.activities_removed} optional paid ${guidance.activities_removed===1?'activity':'activities'}`:''}.</p>{guidance.flight_caveat&&<small>{guidance.flight_caveat}</small>}</section>;
}

function PlanCard({plan, currency, checked, onCompare, onVote, onSelect, onBringUnder}:{plan:Plan;currency:string;checked:boolean;onCompare:()=>void;onVote:(kind:'vote'|'favorite')=>void;onSelect:()=>void;onBringUnder:()=>void}) {
  return <article className={`plan-card ${plan.status === 'selected' ? 'selected' : ''}`}>
    <div className="plan-top"><span className="pill">{plan.summary.pace}</span><label className="compare-check"><input type="checkbox" checked={checked} onChange={onCompare}/> Compare</label></div>
    <h3>{plan.title}</h3><p>{plan.summary.tagline}</p>
    <div className="big-metrics"><div><strong>{money(currency,plan.estimated_total_cost)}</strong><small>{money(currency,plan.estimated_per_person_cost)} / person</small></div><div><strong className={plan.summary.budget_status==='OVER_BUDGET'?'over':''}>{(plan.summary.budget_status||'ESTIMATED').replaceAll('_',' ')}</strong><small>{plan.summary.budget_delta!=null?`${plan.summary.budget_delta>0?'+':''}${money(currency,plan.summary.budget_delta)} vs budget`:'independent plan ledger'}</small></div></div>
    <dl><div><dt>Activities</dt><dd>{plan.activity_count}</dd></div><div><dt>Travel</dt><dd>{plan.travel_minutes} min</dd></div><div><dt>Walking</dt><dd>{plan.walking_intensity}</dd></div><div><dt>Strategy</dt><dd>{(plan.summary.strategy||plan.plan_type).replaceAll('_',' ')}</dd></div></dl>
    {plan.summary.cost_breakdown&&<div className="cost-breakdown">{Object.entries(plan.summary.cost_breakdown).map(([category,row])=><span key={category}><b>{category}</b>{money(currency,row.expected)} <small>{row.price_types.join(' / ')}</small></span>)}</div>}
    <div className="areas">{plan.summary.neighborhoods.slice(0,4).map(area=><span key={area}>{area}</span>)}</div>{Boolean(plan.summary.featured_places?.length)&&<div className="featured-places">{plan.summary.featured_places!.map(place=><div key={`${place.name}-${place.neighborhood}`}><img src={place.image_url||`/placeholders/${place.category}.svg`} alt={place.name}/><span><b>{place.name}</b><small>{place.neighborhood} · {priceLabel(place)}{!['UNKNOWN','PUBLISHED_UNCONVERTED'].includes(place.cost_status)&&` · ${money(place.cost_currency,place.group_cost)} for ${place.traveler_count}`}</small>{place.rating&&<small className="featured-trust">★ {place.rating.toFixed(1)} · {place.review_count?.toLocaleString()||0} reviews · {place.rating_source}</small>}{place.phone_number&&<a href={`tel:${place.phone_number}`}>Call {place.phone_number}</a>}<a href={place.maps_url} target="_blank" rel="noreferrer">Google Maps ↗</a>{(place.ticket_url||place.cost_source||place.official_url)&&<a href={place.ticket_url||place.cost_source||place.official_url||'#'} target="_blank" rel="noreferrer">{['PUBLISHED','LIVE'].includes(place.cost_status)?'Open exact price / tickets ↗':'Check official ticket price ↗'}</a>}</span></div>)}</div>}<p className="tradeoff"><b>Tradeoff:</b> {plan.summary.main_tradeoffs}</p>
    {plan.summary.cost_reasons?.map(reason=><small className="cost-reason" key={reason}>• {reason}</small>)}
    <div className="card-actions"><button className="ghost" onClick={()=>onVote('vote')}>Vote · {plan.vote_count}</button><button className="ghost" onClick={()=>onVote('favorite')}>♡ {plan.favorite_count}</button>{plan.summary.budget_status==='OVER_BUDGET'&&<button className="ghost" onClick={onBringUnder}>Bring under budget</button>}<button onClick={onSelect}>{plan.status === 'selected' ? 'Selected ✓' : 'Select plan'}</button></div>
  </article>;
}

function displayTime(value:string, absoluteMinute?:number) {
  const time = new Date(`2000-01-01T${value}:00`).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
  return absoluteMinute!=null&&absoluteMinute>=24*60 ? `${time} +1 day` : time;
}

function PlaceContextPanel({context,whySelected}:{context?:PlaceContext|null;whySelected:string}) {
  if (!context) return <details className="place-context"><summary>About this place</summary><p>Verified background is not available yet. TripPilot will not invent a story for this venue.</p><div className="context-section"><b>Why TripPilot chose it for you</b><p>{whySelected}</p></div></details>;
  const sections:{label:string;content:string|string[]}[] = [
    {label:'What is this place?',content:context.short_description},
    {label:'Why people visit',content:context.why_visit},
    {label:'Known for',content:context.known_for},
    {label:'History',content:context.historical_significance||''},
    {label:'Culture',content:context.cultural_significance||''},
    {label:'Architecture',content:context.architectural_significance||''},
    {label:'Film / TV',content:context.movie_tv_connections},
    {label:'Interesting facts',content:context.interesting_facts},
    {label:'What to look for',content:context.recommended_visit_focus},
  ].filter(section=>Array.isArray(section.content)?section.content.length:Boolean(section.content));
  return <details className="place-context"><summary>About this place · why visit?</summary><div className="context-grid">{sections.map(section=><section className="context-section" key={section.label}><b>{section.label}</b>{Array.isArray(section.content)?<ul>{section.content.map(item=><li key={item}>{item}</li>)}</ul>:<p>{section.content}</p>}</section>)}<section className="context-section chosen-context"><b>Why TripPilot chose it for you</b><p>{whySelected}</p></section></div>{context.source_records.length>0&&<div className="context-sources"><span>Verified sources</span>{context.source_records.map(source=><a key={`${source.source_url}-${source.source_name}`} href={source.source_url} target="_blank" rel="noreferrer">{source.source_name} ↗</a>)}</div>}</details>;
}

function VenueTrustFacts({venue}:{venue:VenueTrust}) {
  const hasGoogleRating=Boolean(venue.rating&&venue.rating_source==='Google Places');
  return <div className="venue-trust"><span className={hasGoogleRating?'verified':'unavailable'}>{venue.rating?<><b>★ {venue.rating.toFixed(1)} / 5</b><small>{venue.review_count?.toLocaleString()||0} reviews · {venue.rating_source||'provider'}</small></>:<><b>Google rating unavailable</b><small>Add a Maps Platform key, or this listing returned no rating</small></>}</span><span className={venue.phone_number?'verified':'unavailable'}>{venue.phone_number?<><a href={`tel:${venue.phone_number}`}>Call {venue.phone_number}</a><small>{venue.phone_source_url?<a href={venue.phone_source_url} target="_blank" rel="noreferrer">Contact source ↗</a>:'Provider contact'}</small></>:<><b>Phone not provided</b><small>Add a Maps Platform key, or use the Maps / official listing</small></>}</span></div>;
}

function TransportComparison({details}:{details?:TransportDetails|null}) {
  if(!details)return null;
  return <details className="transport-comparison"><summary><span><small>From previous stop · cheapest whole-group option</small><b>{details.selected_mode} · {money(details.currency,details.selected_group_cost)} for {details.traveler_count}</b></span><em>{details.duration_minutes} min · compare</em></summary><p>{details.selection_reason}</p><div>{details.options.map(option=><article className={option.mode===details.selected_mode?'selected':''} key={option.mode}><span><b>{option.mode}{option.mode===details.selected_mode?' ✓':''}</b><small>{option.source_quality.replaceAll('_',' ').toLowerCase()}</small></span><strong>{money(details.currency,option.group_cost)}</strong><small>{option.calculation}</small><small>{option.duration_minutes} min · {option.route_source}</small></article>)}</div><a href={details.directions_url} target="_blank" rel="noreferrer">Open selected route in Google Maps ↗</a><small className="transport-caveat">Cab is an estimate and can change with traffic, surge, tolls, tips, or vehicle size. Transit is live only when labeled Google live fare.</small></details>;
}

type MealAlternative = {restaurant_id:number;name:string;cuisine:string[];neighborhood:string;distance_km:number;expected_cost_per_person:number;group_total:number;cost_delta:number;source_quality:string;photo_url:string;maps_url:string;official_url?:string|null;reservation_url?:string|null;why_selected:string;context?:PlaceContext|null};

function MealTimelineItem({meal,currency,planId,onUpdated}:{meal:MealItem;currency:string;planId:number;onUpdated:(detail:PlanDetail)=>void}) {
  const [alternatives,setAlternatives] = useState<MealAlternative[]>([]); const [working,setWorking] = useState(false);
  const findAlternatives = async()=>{setWorking(true);try{const response=await request<{alternatives:MealAlternative[]}>(`/plans/${planId}/days/${meal.day_number}/meals/${meal.meal_type}/replace`,{method:'POST',body:JSON.stringify({})});setAlternatives(response.alternatives);}catch(error){reportError(error);}finally{setWorking(false);}};
  const choose = async(restaurantId:number)=>{setWorking(true);try{const response=await request<{detail:PlanDetail}>(`/plans/${planId}/days/${meal.day_number}/meals/${meal.meal_type}/replace`,{method:'POST',body:JSON.stringify({restaurant_id:restaurantId})});onUpdated(response.detail);setAlternatives([]);}catch(error){reportError(error);}finally{setWorking(false);}};
  return <article className="detail-item meal-item" id={meal.item_id}><div className="time-rail"><b>{displayTime(meal.start_time,meal.start_minute)}</b><span>{displayTime(meal.end_time,meal.end_minute)}</span><i/></div><figure className="place-photo"><img src={meal.photo_url_or_reference} alt={meal.restaurant_name}/>{meal.photo_source_url&&<figcaption><a href={meal.photo_source_url} target="_blank" rel="noreferrer">{meal.photo_attribution||'Image source'} ↗</a></figcaption>}</figure><div className="item-content"><div className="item-labels"><span className="meal-badge">{meal.meal_type}</span>{meal.cuisine.slice(0,3).map(cuisine=><span key={cuisine}>{cuisine}</span>)}<span>{meal.source_quality.toLowerCase()} price</span></div><h3>{meal.restaurant_name}</h3><p className="location">{meal.neighborhood}, {meal.borough_or_district} · {meal.address}</p><VenueTrustFacts venue={meal}/><TransportComparison details={meal.transport_details}/><div className="item-facts"><span><b>{money(currency,meal.expected_cost_per_person)} each</b>{money(currency,meal.expected_total)} for {meal.traveler_count}</span><span><b>{meal.duration_minutes} min</b> meal time</span><span><b>{meal.travel_minutes_from_previous} min</b> from previous stop</span></div><p className="reason">{meal.reason_selected}</p><div className="link-row"><a href={meal.maps_url} target="_blank" rel="noreferrer">Google Maps ↗</a>{meal.directions_url&&<a href={meal.directions_url} target="_blank" rel="noreferrer">Directions ↗</a>}{meal.menu_url&&<a href={meal.menu_url} target="_blank" rel="noreferrer">Menu ↗</a>}{meal.official_url&&<a href={meal.official_url} target="_blank" rel="noreferrer">Official ↗</a>}{meal.reservation_url&&<a href={meal.reservation_url} target="_blank" rel="noreferrer">Reserve ↗</a>}</div><PlaceContextPanel context={meal.context} whySelected={meal.reason_selected}/><button type="button" className="ghost meal-replace" onClick={findAlternatives} disabled={working}>{working?'Checking nearby restaurants…':'Find a cheaper nearby option'}</button>{alternatives.length>0&&<div className="meal-alternatives"><b>Nearby alternatives</b>{alternatives.map(option=><article key={option.restaurant_id}><img src={option.photo_url} alt=""/><span><strong>{option.name}</strong><small>{option.neighborhood} · {option.distance_km} km away</small><small>{money(currency,option.expected_cost_per_person)} each · {money(currency,option.group_total)} group</small><em className={option.cost_delta<=0?'saves':'costs'}>{option.cost_delta<=0?`${money(currency,Math.abs(option.cost_delta))} less`:`${money(currency,option.cost_delta)} more`}</em></span><button type="button" onClick={()=>choose(option.restaurant_id)} disabled={working}>Use this</button></article>)}</div>}</div></article>;
}

function ExpenseBreakdownDrawer({planId,category,onClose}:{planId:number;category:string;onClose:()=>void}) {
  const [data,setData] = useState<ExpenseBreakdown|null>(null);
  useEffect(()=>{setData(null);request<ExpenseBreakdown>(`/plans/${planId}/expenses/${encodeURIComponent(category)}`).then(setData).catch(reportError);},[planId,category]);
  const label=category==='activities'?'Attractions & tickets':category==='transport'?'Getting around':category;
  return <div className="expense-overlay" role="dialog" aria-modal="true" aria-label={`${label} expense breakdown`}><button className="expense-scrim" aria-label="Close expense breakdown" onClick={onClose}/><aside className="expense-drawer"><button className="drawer-close" onClick={onClose}>Close ×</button>{!data?<div className="drawer-loading">Loading exact calculations…</div>:<><div className="eyebrow">Expense drill-down</div><h2>{label}</h2><div className="drawer-total"><strong>{money(data.currency,data.expected)}</strong><span>Expected group total</span><small>{money(data.currency,data.minimum)} minimum · {money(data.currency,data.maximum)} safe range</small></div>{data.days.map(day=><details className="expense-day" key={day.day} open><summary><span>{day.day==='trip'?'Whole-trip costs':`Day ${day.day}`}</span><b>{money(data.currency,day.expected)}</b></summary>{day.items.map(item=><div className="expense-line" key={item.id}><div><a href={item.itinerary_item_id?`#${item.itinerary_item_id}`:undefined} onClick={onClose}>{item.title}</a><p>{item.description}</p></div><strong>{money(data.currency,item.total_expected)}</strong><code>{item.calculation_expression}</code><small>{item.source_quality.replaceAll('_',' ')}{item.assumptions.length?` · ${item.assumptions.join(' ')}`:''}</small>{item.source_reference&&item.source_reference.startsWith('http')&&<a href={item.source_reference} target="_blank" rel="noreferrer">Price/source evidence ↗</a>}</div>)}</details>)}</>}</aside></div>;
}

function DetailedPlan({detail,onBack}:{detail:PlanDetail;onBack:()=>void}) {
  const [view,setView] = useState(detail); const [activeDay,setActiveDay] = useState(0); const [expenseCategory,setExpenseCategory] = useState<string|null>(null); const day = view.days[activeDay];
  useEffect(()=>setView(detail),[detail]);
  const categoryLabel=(category:string)=>({activities:'Attractions & tickets',transport:'Getting around',buffer:'Emergency cushion',food:'Food · exact meals',flight:'Flights',hotel:'Hotel',nightlife:'Nightlife',shopping:'Shopping'} as Record<string,string>)[category]||category;
  return <section className="detail-view"><button className="back-button" onClick={onBack}>← Back to four plans</button><div className="detail-hero"><div><div className="eyebrow">Selected plan · {view.stage}</div><h2>{view.title}</h2><p>{view.trip.destination} · {view.trip.start_date} → {view.trip.end_date} · {view.trip.traveler_count} travelers</p></div><div className="detail-head-metrics"><div><strong>{money(view.trip.currency,view.budget.expected)}</strong><small>{view.budget.pricing_complete===false?'known-cost group total':'expected group total'}</small></div><div><strong>{money(view.trip.currency,view.budget.expected_per_person)}</strong><small>per person</small></div><span className={view.budget.status === 'WITHIN_BUDGET' ? 'budget-ok' : view.budget.status === 'PRICE_CHECK_REQUIRED' ? 'budget-check' : 'budget-over'}>{view.budget.status.replaceAll('_',' ')}</span></div></div>
    <FlightQuoteCard quote={view.flight_quote}/>
    <div className="estimate-strip"><div><small>Minimum</small><strong>{money(view.trip.currency,view.budget.minimum)}</strong></div><div><small>Expected</small><strong>{money(view.trip.currency,view.budget.expected)}</strong></div><div><small>Safe estimate</small><strong>{money(view.trip.currency,view.budget.safe)}</strong></div><div><small>Budget target</small><strong>{money(view.trip.currency,view.budget.target)}</strong></div></div>
    <div className="budget-dashboard-title"><div><div className="eyebrow">Click any category</div><h3>Where your money goes</h3></div><span>{view.expenses.reconciliation.valid?'✓ Every visible line reconciles':'⚠ Reconciliation needs attention'}</span></div><div className="category-ledger">{view.expenses.categories.map(category=><button type="button" key={category.category} onClick={()=>setExpenseCategory(category.category)}><span>{categoryLabel(category.category)}</span><strong>{money(view.trip.currency,category.expected)}</strong><small>{category.line_count} {category.line_count===1?'line':'lines'} · view calculation →</small></button>)}</div>
    <div className="day-tabs">{view.days.map((item,index)=><button className={activeDay===index?'active':''} key={item.date} onClick={()=>setActiveDay(index)}>Day {item.day_number}<small>{item.region_summary}</small></button>)}</div>
    <div className="day-summary"><div><div className="eyebrow">Day {day.day_number} · {day.date}</div><h3>{day.region_summary}</h3><span>First meal {displayTime(day.hotel_departure_time,day.hotel_departure_minute)} · finish {displayTime(day.finish_time,day.finish_minute)}</span></div><div><b>{money(view.trip.currency,day.expense_total_expected)}</b><span>visible day expenses · {day.travel_minutes} min travel · {day.walking_intensity} walking</span></div></div>
    <div className="timeline">{day.timeline.map(item=>'meal_type' in item?<MealTimelineItem key={item.item_id} meal={item} currency={view.trip.currency} planId={view.plan_id} onUpdated={updated=>setView(updated)}/>:<article className="detail-item" id={item.item_id} key={item.item_id}><div className="time-rail"><b>{displayTime(item.start_time,item.start_minute)}</b><span>{displayTime(item.end_time,item.end_minute)}</span><i/></div><figure className="place-photo"><img src={item.photo_url_or_reference} alt={item.name}/>{item.photo_source_url&&<figcaption><a href={item.photo_source_url} target="_blank" rel="noreferrer">{item.photo_attribution||'Image source'} ↗</a></figcaption>}</figure><div className="item-content"><div className="item-labels"><span>{item.category}</span><span>{item.subcategory}</span><span>{item.opening_status.replaceAll('_',' ')}</span></div><h3>{item.name}</h3><p className="location">{item.neighborhood}, {item.borough_or_district} · {item.address}</p><VenueTrustFacts venue={item}/><TransportComparison details={item.transport_details}/><div className="item-facts"><span><b>{item.cost_status==='FREE'?'Free entry':item.cost_status==='UNKNOWN'?'Price not published':item.cost_status==='PUBLISHED_UNCONVERTED'?`${money(item.published_currency,item.published_cost)} published`:money(item.cost_currency,item.cost_expected)}</b>{['PUBLISHED','LIVE'].includes(item.cost_status)?` per person · ${money(item.cost_currency,item.group_cost_expected)} for ${item.traveler_count}`:item.cost_status==='UNKNOWN'?' · check official/ticket site':item.cost_status==='PUBLISHED_UNCONVERTED'?' · currency conversion unavailable':' estimated per person'}</span><span><b>{item.duration_minutes} min</b> planned · {item.duration_min}–{item.duration_max} min range</span><span><b>{item.travel_minutes_from_previous} min</b> travel{item.break_minutes_after_previous?` + ${item.break_minutes_after_previous} min break`:''}</span><span><b>{item.best_time_of_day}</b> best time</span></div>{item.opening_hours&&<p className="provider-hours"><b>Provider hours:</b> {item.opening_hours}</p>}<div className="link-row"><a href={item.maps_url} target="_blank" rel="noreferrer">Google Maps ↗</a>{item.directions_url&&<a href={item.directions_url} target="_blank" rel="noreferrer">Selected route ↗</a>}{item.official_url&&<a href={item.official_url} target="_blank" rel="noreferrer">Official ↗</a>}{(item.ticket_url||item.cost_source)&&<a href={item.ticket_url||item.cost_source||'#'} target="_blank" rel="noreferrer">{['PUBLISHED','LIVE'].includes(item.cost_status)?'Open exact price / tickets ↗':'Check live ticket price ↗'}</a>}{item.cost_source&&item.cost_source!==item.ticket_url&&<a href={item.cost_source} target="_blank" rel="noreferrer">Published price source ↗</a>}{item.reservation_url&&item.reservation_url!==item.ticket_url&&<a href={item.reservation_url} target="_blank" rel="noreferrer">Reserve ↗</a>}</div><PlaceContextPanel context={item.context} whySelected={item.why_selected}/></div></article>)}</div>
    <div className={`reconciliation-card ${view.expenses.reconciliation.valid?'reconciled':'warning'}`}><b>{view.expenses.reconciliation.valid?'Budget math reconciled':'Budget reconciliation warning'}</b><p>{view.expenses.reconciliation.valid?`${money(view.trip.currency,view.expenses.expected_total)} equals the sum of every category and visible expense line within rounding tolerance.`:'One or more line-item calculations do not match the displayed total.'}</p></div>
    <div className="validation-card"><div><b>{view.validation.valid ? 'Validated itinerary' : 'Validation needs attention'}</b><p>Every claim below comes from a deterministic check or provider record. Missing live prices are shown as unknown, never as free.</p></div><div>{Object.entries(view.validation.checks).map(([key,value])=><span key={key} className={value===true?'pass':'neutral'}>{value===true?'✓':'·'} {key.replaceAll('_',' ')}</span>)}</div><small>{Object.entries(view.data_provenance).map(([key,value])=>`${key}: ${value}`).join(' · ')}</small></div>{expenseCategory&&<ExpenseBreakdownDrawer planId={view.plan_id} category={expenseCategory} onClose={()=>setExpenseCategory(null)}/>}</section>;
}

function TripEditor({trip,onSaved}:{trip:Trip;onSaved:(trip:Trip,plans:Plan[],travelers:Traveler[])=>void}) {
  const [saving,setSaving] = useState(false);
  const [hintValues,setHintValues] = useState({destination:trip.destination_city,origin:trip.origin,start_date:trip.start_date,end_date:trip.end_date,traveler_count:trip.traveler_count,currency:trip.currency,hotel_style:trip.hotel_style||'midrange',flight_included:trip.flight_included!==false});
  const [enteredBudget,setEnteredBudget] = useState(trip.total_budget);
  const submit = async(event:FormEvent<HTMLFormElement>)=>{event.preventDefault();setSaving(true);const data=new FormData(event.currentTarget);try{
    const updated=await request<Trip>(`/trips/${trip.id}`,{method:'PUT',body:JSON.stringify({destination_city:data.get('destination_city'),origin:data.get('origin'),start_date:data.get('start_date'),end_date:data.get('end_date'),traveler_count:Number(data.get('traveler_count')),total_budget:Number(data.get('total_budget')),currency:data.get('currency'),pace:data.get('pace'),transport_mode:data.get('transport_mode'),hotel_style:data.get('hotel_style'),flight_included:data.get('flight_included')==='on'})});
    const plans=await request<Plan[]>(`/trips/${trip.id}/regenerate-plans`,{method:'POST'});const travelers=await request<Traveler[]>(`/trips/${trip.id}/travelers`);onSaved(updated,plans,travelers);
  }catch(error){reportError(error);}finally{setSaving(false);}};
  return <details className="trip-editor"><summary>Edit trip & regenerate</summary><form onSubmit={submit}><div className="edit-grid"><CityInput label="Destination" name="destination_city" initial={trip.destination_city} onValueChange={destination=>setHintValues(current=>({...current,destination}))}/><CityInput label="Origin" name="origin" initial={trip.origin} onValueChange={origin=>setHintValues(current=>({...current,origin}))}/><label>Start<input type="date" name="start_date" defaultValue={trip.start_date} onChange={event=>setHintValues(current=>({...current,start_date:event.target.value}))}/></label><label>End<input type="date" name="end_date" defaultValue={trip.end_date} onChange={event=>setHintValues(current=>({...current,end_date:event.target.value}))}/></label><label>Travelers<input type="number" min="1" max="20" name="traveler_count" defaultValue={trip.traveler_count} onChange={event=>setHintValues(current=>({...current,traveler_count:Number(event.target.value)}))}/></label><div className="budget-field"><label>Budget<input type="number" min="1" name="total_budget" defaultValue={trip.total_budget} onChange={event=>setEnteredBudget(Number(event.target.value))}/></label><BudgetMinimumHint values={hintValues} enteredBudget={enteredBudget} compact/></div><label>Currency<select name="currency" defaultValue={trip.currency} onChange={event=>setHintValues(current=>({...current,currency:event.target.value}))}>{['USD','INR','GBP','EUR','CAD','AUD','JPY'].map(value=><option key={value}>{value}</option>)}</select></label><label>Pace<select name="pace" defaultValue={trip.pace||'balanced'}><option value="balanced">Balanced</option><option value="relaxed">Relaxed</option><option value="packed">Packed</option></select></label><label>Transport<select name="transport_mode" defaultValue={trip.transport_mode||'mixed'}><option value="mixed">Cheapest of cab / transit</option><option value="walking">Walking</option><option value="public transit">Public transit</option><option value="cab">Cab / taxi</option></select></label><label>Hotel<select name="hotel_style" defaultValue={trip.hotel_style||'midrange'} onChange={event=>setHintValues(current=>({...current,hotel_style:event.target.value}))}><option value="budget">Budget</option><option value="midrange">Midrange</option><option value="premium">Premium</option><option value="luxury">Luxury</option></select></label><label className="flight-toggle"><input type="checkbox" name="flight_included" defaultChecked={trip.flight_included!==false} onChange={event=>setHintValues(current=>({...current,flight_included:event.target.checked}))}/> Include flights</label></div><button disabled={saving}>{saving?'Saving changes · fetching places · repricing · rebuilding…':'Save & regenerate four plans →'}</button></form></details>;
}

function Plans({trip, travelers, initialPlans=[],onTripUpdated}:{trip:Trip; travelers:Traveler[];initialPlans?:Plan[];onTripUpdated?:(trip:Trip,travelers:Traveler[])=>void}) {
  const [plans,setPlans] = useState<Plan[]>(initialPlans); const [voter,setVoter] = useState<number>(travelers[0]?.id); const [compare,setCompare] = useState<number[]>([]); const [loading,setLoading] = useState(false); const [detail,setDetail] = useState<PlanDetail|null>(null); const [detailLoading,setDetailLoading] = useState(false);
  const refresh = async () => setPlans((await request<Plan[]>(`/trips/${trip.id}/plans`)).filter(plan=>plan.summary.engine!=='cp_sat'));
  useEffect(()=>{const onFocus=()=>{refresh().catch(()=>undefined)};window.addEventListener('focus',onFocus);window.addEventListener('advanced-plans-updated',onFocus);return()=>{window.removeEventListener('focus',onFocus);window.removeEventListener('advanced-plans-updated',onFocus);};},[trip.id]);
  const generate = async () => {setLoading(true); try{setPlans((await request<Plan[]>(`/trips/${trip.id}/generate-plans`, {method:'POST'})).filter(plan=>plan.summary.engine!=='cp_sat'));window.dispatchEvent(new Event('advanced-plans-updated'));} catch(error){reportError(error);} finally{setLoading(false);}};
  const vote = async (planId:number, vote_type:'vote'|'favorite') => {try{await request(`/plans/${planId}/vote`, {method:'POST', body:JSON.stringify({traveler_id:voter, vote_type})}); await refresh();} catch(error){reportError(error);}};
  const select = async (planId:number) => {setDetailLoading(true);try{await request(`/plans/${planId}/select`, {method:'POST'}); await refresh(); try{setDetail(await request<PlanDetail>(`/plans/${planId}/detail`));}catch{setDetail(await request<PlanDetail>(`/plans/${planId}/generate-detail`,{method:'POST'}));}} catch(error){reportError(error);} finally{setDetailLoading(false);}};
  const bringUnder = async(planId:number)=>{try{const result=await request<{possible?:boolean;message?:string}>(`/plans/${planId}/bring-under-budget`,{method:'POST'});if(result.possible===false)throw new Error(result.message||'This plan cannot fit while keeping required items.');await refresh();}catch(error){reportError(error);}};
  const toggleCompare = (id:number) => setCompare(current => current.includes(id) ? current.filter(item=>item!==id) : current.length < 2 ? [...current,id] : [current[1],id]);
  if (detail) return <DetailedPlan detail={detail} onBack={()=>setDetail(null)}/>;
  if (!plans.length) return <section className="generate"><div><div className="eyebrow">04 · Plan strategies</div><h2>Ready for four ways to go?</h2><p>The same planning engine will optimize for balance, experiences, budget, and convenience.</p></div><button onClick={generate} disabled={loading}>{loading ? 'Building plans…' : 'Generate four plans →'}</button></section>;
  if (detailLoading) return <section className="detail-loading"><div className="spinner"/><div><div className="eyebrow">Selected</div><h2>Building your detailed trip…</h2><p>Finding exact places and local meals · retrieving place stories · checking hours, photos, and links · estimating line-item costs · optimizing routes · reconciling the budget · validating</p></div></section>;
  const budgetGuidance = plans.find(plan=>plan.plan_type==='budget')?.summary.budget_guidance;
  return <section className="plans-section"><div className="plans-heading"><div><div className="eyebrow">04 · Compare & decide</div><h2>Four good trips. Different tradeoffs.</h2></div><label>Voting as<select value={voter} onChange={event=>setVoter(Number(event.target.value))}>{travelers.map(t=><option key={t.id} value={t.id}>{t.display_name}</option>)}</select></label></div><TripEditor trip={trip} onSaved={(updated,newPlans,newTravelers)=>{setPlans(newPlans.filter(plan=>plan.summary.engine!=='cp_sat'));window.dispatchEvent(new Event('advanced-plans-updated'));setCompare([]);setDetail(null);setVoter(newTravelers[0]?.id);onTripUpdated?.(updated,newTravelers);}}/><BudgetGuidanceCard guidance={budgetGuidance} currency={trip.currency}/><FlightQuoteCard quote={plans[0]?.summary.flight_quote} trip={trip}/><Compare plans={compare.map(id=>plans.find(plan=>plan.id===id)!).filter(Boolean)} currency={trip.currency}/><div className="plans-grid">{plans.map(plan=><PlanCard key={plan.id} plan={plan} currency={trip.currency} checked={compare.includes(plan.id)} onCompare={()=>toggleCompare(plan.id)} onVote={kind=>vote(plan.id,kind)} onBringUnder={()=>bringUnder(plan.id)} onSelect={()=>select(plan.id)}/>)}</div></section>;
}

function SavedTrips({onOpen}:{onOpen:(trip:Trip)=>void}) {
  const [trips,setTrips]=useState<Trip[]>([]);
  useEffect(()=>{request<Trip[]>('/v1/trips').then(setTrips).catch(reportError);},[]);
  if(!trips.length)return null;
  return <section className="panel"><h2>Your saved trips</h2><div className="saved-trips">{trips.map(trip=><button type="button" key={trip.id} onClick={()=>onOpen(trip)}><strong>{trip.destination_city}</strong><span>{trip.start_date} → {trip.end_date} · {trip.traveler_count} travelers</span></button>)}</div></section>;
}

function App() {
  const [trip,setTrip] = useState<Trip|null>(null); const [travelers,setTravelers] = useState<Traveler[]>([]); const [budgetSaved,setBudgetSaved] = useState(false); const [initialPlans,setInitialPlans] = useState<Plan[]>([]); const [error,setError] = useState(''); const [draft,setDraft] = useState<TripDraft>({});
  useEffect(() => {
    const report = (event:Event) => setError((event as CustomEvent<string>).detail);
    window.addEventListener('trip-error', report);
    return () => window.removeEventListener('trip-error', report);
  }, []);
  return <main><header><nav><span>TripPilot AI</span><em>Group travel, optimized.</em></nav><h1>Find the trip<br/><i>everyone</i> wants.</h1><p>Bring preferences, budgets, and tradeoffs into one clear group decision.</p></header>
    {error && <div className="error" role="alert"><span>{error}</span><button onClick={()=>setError('')}>Dismiss</button></div>}
    {!trip ? <><SavedTrips onOpen={async saved=>{try{const [group,plans]=await Promise.all([request<Traveler[]>(`/trips/${saved.id}/travelers`),request<Plan[]>(`/trips/${saved.id}/plans`)]);setTrip(saved);setTravelers(group);setInitialPlans(plans);setBudgetSaved(true);}catch(error){reportError(error);}}}/><NaturalTripInput onState={setDraft} onGenerated={result=>{setTrip(result.trip);setTravelers(result.travelers);setBudgetSaved(true);setInitialPlans(result.plans);}}/><div className="form-divider"><span>Review or enter manually</span></div><TripForm key={JSON.stringify(draft)} onCreated={created=>{setTrip(created);setInitialPlans([]);}} draft={draft}/></> : <><section className="trip-strip"><button type="button" onClick={()=>{setTrip(null);setTravelers([]);setInitialPlans([]);setBudgetSaved(false);setDraft({});}}>Your trips</button><div><small>Destination</small><strong>{trip.destination_city}</strong></div><div><small>From</small><strong>{trip.origin}</strong></div><div><small>Dates</small><strong>{trip.start_date} → {trip.end_date}</strong></div><div><small>Travelers</small><strong>{trip.traveler_count}</strong></div><div><small>Budget</small><strong>{money(trip.currency,trip.total_budget)}</strong></div></section>{travelers.length===trip.traveler_count&&<AdvancedPlanner key={trip.id} trip={trip} api={request}/>} {initialPlans.filter(plan=>plan.summary.engine!=='cp_sat').length?<Plans trip={trip} travelers={travelers} initialPlans={initialPlans.filter(plan=>plan.summary.engine!=='cp_sat')} onTripUpdated={(updated,group)=>{setTrip(updated);setTravelers(group);}}/>:<><TravelerForm trip={trip} travelers={travelers} onAdded={traveler=>setTravelers(current=>[...current,traveler])}/><BudgetForm trip={trip} onSaved={()=>setBudgetSaved(true)}/>{travelers.length === trip.traveler_count && budgetSaved && <Plans trip={trip} travelers={travelers} onTripUpdated={(updated,group)=>{setTrip(updated);setTravelers(group);}}/>}</>}</>}
  </main>;
}

type Account = {id:string;email:string;display_name:string};

function AccountApp() {
  const [user,setUser] = useState<Account|null>(null);
  const [loading,setLoading] = useState(true);
  const [register,setRegister] = useState(false);
  const [saving,setSaving] = useState(false);
  const [error,setError] = useState('');
  useEffect(()=>{
    request<Account>('/v1/auth/me').then(setUser).catch(()=>setUser(null)).finally(()=>setLoading(false));
    const expired=()=>{setUser(null);setError('Your session expired. Sign in to continue.');};
    window.addEventListener('session-expired',expired);
    return()=>window.removeEventListener('session-expired',expired);
  },[]);
  const submit=async(event:FormEvent<HTMLFormElement>)=>{
    event.preventDefault();setSaving(true);setError('');
    const data=Object.fromEntries(new FormData(event.currentTarget));
    try {const result=await request<{user:Account}>(`/v1/auth/${register?'register':'login'}`,{method:'POST',body:JSON.stringify(data)});setUser(result.user);}
    catch(error){setError(error instanceof Error?error.message:'Unable to sign in.');}
    finally{setSaving(false);}
  };
  if(loading)return <main><p role="status">Loading your account…</p></main>;
  if(user)return <><div className="account-bar"><span>{user.display_name}</span><button onClick={async()=>{try{await request('/v1/auth/logout',{method:'POST'});setUser(null);}catch(error){reportError(error);}}}>Sign out</button></div><App key={user.id}/></>;
  return <main className="auth-page"><header><nav><span>TripPilot AI</span></nav><h1>Your next trip<br/><i>starts here.</i></h1><p>Save your plans and decide together.</p></header><form className="panel auth-form" onSubmit={submit}><h2>{register?'Create your account':'Welcome back'}</h2>{error&&<p role="alert" className="error">{error}</p>}{register&&<label>Your name<input name="display_name" required maxLength={100} autoComplete="name"/></label>}<label>Email<input name="email" type="email" required autoComplete="email" maxLength={254}/></label><label>Password<input name="password" type="password" required minLength={12} maxLength={128} autoComplete={register?'new-password':'current-password'}/></label>{register&&<small>Use at least 12 characters.</small>}<button disabled={saving}>{saving?'Please wait…':register?'Create account':'Sign in'}</button><button type="button" className="secondary" onClick={()=>{setRegister(!register);setError('');}}>{register?'Already have an account? Sign in':'New here? Create an account'}</button></form></main>;
}

createRoot(document.getElementById('root')!).render(<AccountApp/>);
